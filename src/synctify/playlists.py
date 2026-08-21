from __future__ import annotations

from dataclasses import dataclass
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

    @property
    def written(self) -> int:
        return sum(result.output is not None for result in self.results)

    @property
    def incomplete(self) -> int:
        return sum(not result.complete for result in self.results)


def safe_playlist_filename(name: str) -> str:
    cleaned = re.sub(r"[/:]", "_", name).strip()
    return cleaned or "Untitled Playlist"


def render_m3u8(playlist: Playlist, playlist_dir: Path) -> str:
    lines = ["#EXTM3U"]
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


def build_playlists(
    connection: sqlite3.Connection,
    playlist_dir: Path,
    *,
    allow_partial: bool = False,
) -> PlaylistBuildReport:
    playlists = playlists_from_database(connection)
    filenames = _output_filenames(playlists)
    results: list[PlaylistBuildResult] = []

    for playlist in playlists:
        available: list[Track] = []
        missing: list[str] = []
        for track in playlist.tracks:
            if track.local_path is None or not track.local_path.is_file():
                missing.append(f"{track.artist} - {track.title}")
            else:
                available.append(track)

        output: Path | None = None
        if not missing or (allow_partial and available):
            write_playlist = Playlist(
                spotify_id=playlist.spotify_id,
                name=playlist.name,
                tracks=tuple(available),
                snapshot_id=playlist.snapshot_id,
            )
            output = write_m3u8(
                write_playlist,
                playlist_dir,
                filename=filenames[playlist.spotify_id],
            )

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

    return PlaylistBuildReport(tuple(results))


def format_build_report(report: PlaylistBuildReport) -> str:
    if not report.results:
        return "No playlists found."
    lines = [
        f"Playlists written: {report.written}/{len(report.results)}",
        f"Incomplete:        {report.incomplete}",
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
    return "\n".join(lines)
