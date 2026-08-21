from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3

from .models import Playlist, Track


class MissingLocalTrackError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class PlaylistBuildResult:
    spotify_id: str
    name: str
    output: Path | None
    total_tracks: int
    written_tracks: int
    missing_tracks: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_tracks


@dataclass(slots=True, frozen=True)
class PlaylistBuildReport:
    results: tuple[PlaylistBuildResult, ...]
    removed_outputs: tuple[Path, ...] = ()
    protected_outputs: tuple[Path, ...] = ()

    @property
    def written(self) -> int:
        return sum(result.output is not None for result in self.results)

    @property
    def incomplete(self) -> int:
        return sum(not result.complete for result in self.results)


@dataclass(slots=True, frozen=True)
class GeneratedPlaylistOwnership:
    playlist_id: str
    filename: str


def safe_playlist_filename(name: str) -> str:
    cleaned = re.sub(r"[/:]", "_", name).strip()
    return cleaned or "Untitled Playlist"


def playlist_marker(spotify_id: str) -> str:
    return f"#SYNCTIFY:playlist-id={spotify_id}"


def render_m3u8(playlist: Playlist, playlist_dir: Path) -> str:
    lines = ["#EXTM3U", playlist_marker(playlist.spotify_id)]
    for track in playlist.tracks:
        if track.local_path is None:
            raise MissingLocalTrackError(
                f"{track.artist} - {track.title} has no local file"
            )
        relative = os.path.relpath(track.local_path, start=playlist_dir)
        lines.append(Path(relative).as_posix())
    return "\n".join(lines) + "\n"


def write_m3u8(playlist: Playlist, playlist_dir: Path, *, filename: str | None = None) -> Path:
    playlist_dir.mkdir(parents=True, exist_ok=True)
    output = playlist_dir / (filename or f"{safe_playlist_filename(playlist.name)}.m3u8")
    output.write_text(render_m3u8(playlist, playlist_dir), encoding="utf-8", newline="\n")
    return output


def _playlist_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            p.spotify_id AS playlist_id,
            p.name AS playlist_name,
            pt.position,
            t.spotify_id AS track_id,
            t.title,
            t.artist,
            t.album,
            t.isrc,
            t.duration_ms,
            t.local_path
        FROM playlists AS p
        LEFT JOIN playlist_tracks AS pt ON pt.playlist_id = p.spotify_id
        LEFT JOIN tracks AS t ON t.spotify_id = pt.track_id
        ORDER BY p.name COLLATE NOCASE, p.spotify_id, pt.position
        """
    ).fetchall()


def playlists_from_database(connection: sqlite3.Connection) -> tuple[Playlist, ...]:
    grouped: dict[str, tuple[str, list[Track]]] = {}
    for row in _playlist_rows(connection):
        playlist_id = row["playlist_id"]
        name, tracks = grouped.setdefault(playlist_id, (row["playlist_name"], []))
        if row["track_id"] is None:
            continue
        local_path = Path(row["local_path"]) if row["local_path"] else None
        tracks.append(
            Track(
                spotify_id=row["track_id"],
                title=row["title"],
                artist=row["artist"],
                album=row["album"],
                isrc=row["isrc"],
                duration_ms=row["duration_ms"],
                local_path=local_path,
            )
        )
    return tuple(
        Playlist(spotify_id=playlist_id, name=name, tracks=tuple(tracks))
        for playlist_id, (name, tracks) in grouped.items()
    )


def _output_filenames(playlists: tuple[Playlist, ...]) -> dict[str, str]:
    by_name: dict[str, list[Playlist]] = {}
    for playlist in playlists:
        by_name.setdefault(safe_playlist_filename(playlist.name), []).append(playlist)

    filenames: dict[str, str] = {}
    for safe_name, matches in by_name.items():
        if len(matches) == 1:
            filenames[matches[0].spotify_id] = f"{safe_name}.m3u8"
            continue
        for playlist in matches:
            suffix = re.sub(r"[^A-Za-z0-9]", "", playlist.spotify_id)[-8:] or "playlist"
            filenames[playlist.spotify_id] = f"{safe_name} [{suffix}].m3u8"
    return filenames


def _ownerships(connection: sqlite3.Connection) -> dict[str, GeneratedPlaylistOwnership]:
    rows = connection.execute(
        "SELECT playlist_id, filename FROM generated_playlists ORDER BY playlist_id"
    ).fetchall()
    return {
        row["playlist_id"]: GeneratedPlaylistOwnership(row["playlist_id"], row["filename"])
        for row in rows
    }


def _safe_owned_path(playlist_dir: Path, filename: str) -> Path | None:
    relative = Path(filename)
    if relative.name != filename or relative.suffix.lower() != ".m3u8":
        return None
    root = playlist_dir.expanduser().resolve()
    candidate = root / filename
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    if resolved.parent != root:
        return None
    return resolved


def _has_playlist_marker(path: Path, spotify_id: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline().rstrip("\r\n")
            second = handle.readline().rstrip("\r\n")
    except (OSError, UnicodeError):
        return False
    return first == "#EXTM3U" and second == playlist_marker(spotify_id)


def _remove_owned_file(
    playlist_dir: Path,
    ownership: GeneratedPlaylistOwnership,
) -> tuple[Path | None, Path | None]:
    path = _safe_owned_path(playlist_dir, ownership.filename)
    if path is None or not path.exists():
        return None, None
    if not _has_playlist_marker(path, ownership.playlist_id):
        return None, path
    try:
        path.unlink()
    except OSError:
        return None, path
    return path, None


def _drop_ownership(connection: sqlite3.Connection, playlist_id: str) -> None:
    connection.execute(
        "DELETE FROM generated_playlists WHERE playlist_id = ?",
        (playlist_id,),
    )


def _record_ownership(connection: sqlite3.Connection, playlist_id: str, filename: str) -> None:
    connection.execute(
        """
        INSERT INTO generated_playlists(playlist_id, filename, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(playlist_id) DO UPDATE SET
            filename = excluded.filename,
            updated_at = excluded.updated_at
        """,
        (playlist_id, filename, datetime.now(timezone.utc).isoformat()),
    )


def _fallback_filename(playlist: Playlist, desired: str, occupied: dict[str, str], playlist_dir: Path) -> str:
    stem = Path(desired).stem
    suffix = re.sub(r"[^A-Za-z0-9]", "", playlist.spotify_id)[-8:] or "playlist"
    index = 1
    while True:
        extra = "" if index == 1 else f"-{index}"
        candidate = f"{stem} [synctify-{suffix}{extra}].m3u8"
        owner = occupied.get(candidate)
        path = _safe_owned_path(playlist_dir, candidate)
        if owner not in {None, playlist.spotify_id}:
            index += 1
            continue
        if path is None or not path.exists() or _has_playlist_marker(path, playlist.spotify_id):
            return candidate
        index += 1


def _select_output_filename(
    playlist: Playlist,
    desired: str,
    occupied: dict[str, str],
    playlist_dir: Path,
) -> str:
    owner = occupied.get(desired)
    path = _safe_owned_path(playlist_dir, desired)
    if owner not in {None, playlist.spotify_id}:
        return _fallback_filename(playlist, desired, occupied, playlist_dir)
    if path is None or not path.exists():
        return desired
    if _has_playlist_marker(path, playlist.spotify_id):
        return desired
    return _fallback_filename(playlist, desired, occupied, playlist_dir)


def build_playlists(
    connection: sqlite3.Connection,
    playlist_dir: Path,
    *,
    allow_partial: bool = False,
) -> PlaylistBuildReport:
    playlists = playlists_from_database(connection)
    filenames = _output_filenames(playlists)
    ownerships = _ownerships(connection)
    occupied = {item.filename: item.playlist_id for item in ownerships.values()}
    current_ids = {playlist.spotify_id for playlist in playlists}
    removed_outputs: list[Path] = []
    protected_outputs: list[Path] = []
    results: list[PlaylistBuildResult] = []

    for playlist_id, ownership in tuple(ownerships.items()):
        if playlist_id in current_ids:
            continue
        removed, protected = _remove_owned_file(playlist_dir, ownership)
        if removed is not None:
            removed_outputs.append(removed)
        if protected is not None:
            protected_outputs.append(protected)
        _drop_ownership(connection, playlist_id)
        occupied.pop(ownership.filename, None)
        ownerships.pop(playlist_id, None)

    for playlist in playlists:
        available: list[Track] = []
        missing: list[str] = []
        for track in playlist.tracks:
            if track.local_path is None or not track.local_path.is_file():
                missing.append(f"{track.artist} - {track.title}")
            else:
                available.append(track)

        old_ownership = ownerships.get(playlist.spotify_id)
        output: Path | None = None
        should_write = not missing or (allow_partial and available)

        if should_write:
            desired = filenames[playlist.spotify_id]
            selected = _select_output_filename(playlist, desired, occupied, playlist_dir)
            write_playlist = Playlist(
                spotify_id=playlist.spotify_id,
                name=playlist.name,
                tracks=tuple(available),
                snapshot_id=playlist.snapshot_id,
            )
            output = write_m3u8(
                write_playlist,
                playlist_dir,
                filename=selected,
            )
            _record_ownership(connection, playlist.spotify_id, selected)

            if old_ownership is not None and old_ownership.filename != selected:
                removed, protected = _remove_owned_file(playlist_dir, old_ownership)
                if removed is not None:
                    removed_outputs.append(removed)
                if protected is not None:
                    protected_outputs.append(protected)
                occupied.pop(old_ownership.filename, None)
            occupied[selected] = playlist.spotify_id
            ownerships[playlist.spotify_id] = GeneratedPlaylistOwnership(
                playlist.spotify_id,
                selected,
            )
        elif old_ownership is not None:
            removed, protected = _remove_owned_file(playlist_dir, old_ownership)
            if removed is not None:
                removed_outputs.append(removed)
            if protected is not None:
                protected_outputs.append(protected)
            _drop_ownership(connection, playlist.spotify_id)
            occupied.pop(old_ownership.filename, None)
            ownerships.pop(playlist.spotify_id, None)

        results.append(
            PlaylistBuildResult(
                spotify_id=playlist.spotify_id,
                name=playlist.name,
                output=output,
                total_tracks=len(playlist.tracks),
                written_tracks=len(available) if output is not None else 0,
                missing_tracks=tuple(missing),
            )
        )

    return PlaylistBuildReport(
        tuple(results),
        tuple(removed_outputs),
        tuple(protected_outputs),
    )


def format_build_report(report: PlaylistBuildReport) -> str:
    if not report.results and not report.removed_outputs and not report.protected_outputs:
        return "No playlists found."
    lines = [
        f"Playlists written: {report.written}/{len(report.results)}",
        f"Incomplete:        {report.incomplete}",
        f"Stale removed:     {len(report.removed_outputs)}",
        f"Protected:         {len(report.protected_outputs)}",
    ]
    for result in report.results:
        if result.complete:
            lines.append(f"  OK {result.name}: {result.written_tracks}/{result.total_tracks}")
        elif result.output is None:
            lines.append(f"  SKIP {result.name}: missing {len(result.missing_tracks)} track(s)")
        else:
            lines.append(
                f"  PARTIAL {result.name}: {result.written_tracks}/{result.total_tracks}, "
                f"missing {len(result.missing_tracks)}"
            )
    for path in report.protected_outputs:
        lines.append(f"  PROTECTED {path}: ownership marker missing or file could not be safely removed")
    return "\n".join(lines)
