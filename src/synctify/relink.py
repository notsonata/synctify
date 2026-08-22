from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import sqlite3

from .acquisition import file_sha256
from .models import Track
from .providers.reconcile import read_flac_candidate
from .resolution import MatchMethod, ResolutionStatus, resolve_track


class RelinkError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class RelinkMatch:
    spotify_id: str
    title: str
    artist: str
    source_path: Path
    destination_path: Path
    action: str
    match_method: str
    confidence: float
    sha256: str


@dataclass(slots=True, frozen=True)
class RelinkUnmatched:
    spotify_id: str
    title: str
    artist: str
    reason: str


@dataclass(slots=True, frozen=True)
class RelinkFailure:
    spotify_id: str | None
    path: Path | None
    message: str


@dataclass(slots=True, frozen=True)
class RelinkReport:
    source_root: Path
    library_root: Path
    scanned_flacs: int
    target_tracks: int
    already_local: int
    matches: tuple[RelinkMatch, ...]
    unmatched: tuple[RelinkUnmatched, ...]
    failures: tuple[RelinkFailure, ...]
    applied: bool

    @property
    def copied(self) -> int:
        return sum(match.action == "copy" for match in self.matches)

    @property
    def adopted(self) -> int:
        return sum(match.action == "adopt" for match in self.matches)

    @property
    def reused(self) -> int:
        return sum(match.action == "reuse" for match in self.matches)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _track_from_row(row: sqlite3.Row) -> Track:
    return Track(
        spotify_id=row["spotify_id"],
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        isrc=row["isrc"],
        duration_ms=row["duration_ms"],
    )


def _desired_rows(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
    return tuple(
        connection.execute(
            """
            SELECT
                t.spotify_id,
                t.title,
                t.artist,
                t.album,
                t.isrc,
                t.duration_ms,
                t.local_path,
                t.sha256,
                r.provider AS resolution_provider,
                r.provider_track_id AS resolution_track_id
            FROM tracks AS t
            LEFT JOIN track_resolutions AS r ON r.spotify_id = t.spotify_id
            WHERE EXISTS (
                SELECT 1
                FROM playlist_tracks AS pt
                WHERE pt.track_id = t.spotify_id
            )
            ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
            """
        ).fetchall()
    )


def _usable_canonical_path(row: sqlite3.Row, library_root: Path) -> bool:
    raw = row["local_path"]
    if not raw:
        return False
    try:
        path = Path(raw).expanduser().resolve()
    except OSError:
        return False
    return _within(path, library_root) and path.is_file()


def _scan_flacs(source_root: Path) -> tuple[Path, ...]:
    safe: set[Path] = set()
    for path in source_root.rglob("*"):
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if not _within(resolved, source_root):
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".flac":
            safe.add(resolved)
    return tuple(sorted(safe))


def _collision_destination(destination: Path, spotify_id: str) -> Path:
    return destination.with_name(
        f"{destination.stem} [synctify-{spotify_id[:8]}]{destination.suffix}"
    )


def _destination_for(
    source_path: Path,
    source_root: Path,
    library_root: Path,
    spotify_id: str,
    source_sha256: str,
) -> tuple[Path, str]:
    if _within(source_path, library_root):
        return source_path, "adopt"

    relative = source_path.relative_to(source_root)
    destination = (library_root / relative).resolve(strict=False)
    if not _within(destination, library_root):
        raise RelinkError(f"unsafe destination outside canonical library: {destination}")

    if not destination.exists():
        return destination, "copy"
    if destination.is_file():
        try:
            if file_sha256(destination) == source_sha256:
                return destination, "reuse"
        except OSError as exc:
            raise RelinkError(f"could not hash existing destination {destination}: {exc}") from exc

    alternate = _collision_destination(destination, spotify_id)
    if not alternate.exists():
        return alternate, "copy"
    if alternate.is_file():
        try:
            if file_sha256(alternate) == source_sha256:
                return alternate, "reuse"
        except OSError as exc:
            raise RelinkError(f"could not hash existing destination {alternate}: {exc}") from exc
    raise RelinkError(
        f"destination collision for {source_path}; neither {destination} nor {alternate} is safe to replace"
    )


def _record_local_file(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    path: Path,
    sha256: str,
) -> None:
    qobuz_id = (
        row["resolution_track_id"]
        if row["resolution_provider"] == "qobuz"
        else None
    )
    if qobuz_id is not None:
        connection.execute(
            """
            UPDATE tracks
            SET qobuz_id = ?, local_path = ?, sha256 = ?, status = 'local'
            WHERE spotify_id = ?
            """,
            (qobuz_id, str(path), sha256, row["spotify_id"]),
        )
    else:
        connection.execute(
            """
            UPDATE tracks
            SET local_path = ?, sha256 = ?, status = 'local'
            WHERE spotify_id = ?
            """,
            (str(path), sha256, row["spotify_id"]),
        )


def _copy_atomic(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.synctify-relink.tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(source, temporary)
        copied_sha = file_sha256(temporary)
        if copied_sha != expected_sha256:
            raise RelinkError(f"copy verification failed for {source}")
        if destination.exists():
            if not destination.is_file() or file_sha256(destination) != expected_sha256:
                raise RelinkError(f"destination changed during relink: {destination}")
            temporary.unlink()
            return
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def relink_library(
    connection: sqlite3.Connection,
    source: Path,
    library_dir: Path,
    *,
    apply: bool = False,
    limit: int | None = None,
) -> RelinkReport:
    """Safely match desired tracks to an existing FLAC tree and optionally adopt/copy them."""
    source_root = source.expanduser().resolve()
    library_root = library_dir.expanduser().resolve()
    if not source_root.exists():
        raise RelinkError(f"relink source does not exist: {source_root}")
    if not source_root.is_dir():
        raise RelinkError(f"relink source is not a directory: {source_root}")
    if limit is not None and limit < 1:
        raise RelinkError("limit must be at least 1")

    rows = _desired_rows(connection)
    already_local = sum(_usable_canonical_path(row, library_root) for row in rows)
    all_target_rows = [row for row in rows if not _usable_canonical_path(row, library_root)]
    target_rows = all_target_rows if limit is None else all_target_rows[:limit]
    selected_ids = {row["spotify_id"] for row in target_rows}

    source_paths = _scan_flacs(source_root)
    candidates = tuple(
        candidate
        for path in source_paths
        if (candidate := read_flac_candidate(path)) is not None
    )

    # Resolve every missing desired track before applying --limit. The limit controls
    # expensive hashing/copying, but one-to-one source ownership must be global across
    # all desired tracks or separate limited runs could reuse the same source FLAC.
    provisional: dict[str, tuple[sqlite3.Row, Path, MatchMethod, float]] = {}
    path_users: dict[Path, list[str]] = {}
    unmatched: list[RelinkUnmatched] = []
    failures: list[RelinkFailure] = []

    for row in all_target_rows:
        resolution = resolve_track(_track_from_row(row), candidates)
        if resolution.status is not ResolutionStatus.RESOLVED or resolution.candidate is None:
            unmatched.append(
                RelinkUnmatched(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    resolution.reason,
                )
            )
            continue
        try:
            matched_path = Path(resolution.candidate.provider_track_id).resolve()
        except OSError as exc:
            failures.append(RelinkFailure(row["spotify_id"], None, str(exc)))
            continue
        if not _within(matched_path, source_root) or matched_path not in source_paths:
            failures.append(
                RelinkFailure(row["spotify_id"], matched_path, "matcher returned an unsafe source path")
            )
            continue
        method = resolution.method or MatchMethod.METADATA
        provisional[row["spotify_id"]] = (row, matched_path, method, resolution.confidence)
        path_users.setdefault(matched_path, []).append(row["spotify_id"])

    for path, spotify_ids in path_users.items():
        if len(spotify_ids) <= 1:
            continue
        for spotify_id in spotify_ids:
            row = provisional.pop(spotify_id)[0]
            unmatched.append(
                RelinkUnmatched(
                    spotify_id,
                    row["title"],
                    row["artist"],
                    "one source FLAC matched multiple desired tracks; refusing shared automatic assignment",
                )
            )

    # Only selected tracks are reported/applied, but conflicts were computed against
    # the entire desired missing set above.
    provisional = {
        spotify_id: value
        for spotify_id, value in provisional.items()
        if spotify_id in selected_ids
    }
    unmatched = [item for item in unmatched if item.spotify_id in selected_ids]
    failures = [
        item
        for item in failures
        if item.spotify_id is None or item.spotify_id in selected_ids
    ]

    matches: list[RelinkMatch] = []
    rows_by_id = {row["spotify_id"]: row for row in target_rows}
    for spotify_id, (row, source_path, method, confidence) in sorted(provisional.items()):
        try:
            sha256 = file_sha256(source_path)
            destination, action = _destination_for(
                source_path,
                source_root,
                library_root,
                spotify_id,
                sha256,
            )
        except (OSError, RelinkError) as exc:
            failures.append(RelinkFailure(spotify_id, source_path, str(exc)))
            continue
        matches.append(
            RelinkMatch(
                spotify_id=spotify_id,
                title=row["title"],
                artist=row["artist"],
                source_path=source_path,
                destination_path=destination,
                action=action,
                match_method=method.value,
                confidence=confidence,
                sha256=sha256,
            )
        )

    if apply:
        applied_matches: list[RelinkMatch] = []
        for match in matches:
            row = rows_by_id[match.spotify_id]
            try:
                if match.action == "copy":
                    _copy_atomic(match.source_path, match.destination_path, match.sha256)
                elif match.action == "reuse":
                    if not match.destination_path.is_file():
                        raise RelinkError(f"reused destination is missing: {match.destination_path}")
                    if file_sha256(match.destination_path) != match.sha256:
                        raise RelinkError(f"reused destination changed: {match.destination_path}")
                else:
                    if not match.source_path.is_file():
                        raise RelinkError(f"source file disappeared: {match.source_path}")
                    if file_sha256(match.source_path) != match.sha256:
                        raise RelinkError(f"source file changed during relink: {match.source_path}")
                _record_local_file(connection, row, match.destination_path, match.sha256)
                applied_matches.append(match)
            except (OSError, RelinkError) as exc:
                failures.append(RelinkFailure(match.spotify_id, match.source_path, str(exc)))
        matches = applied_matches

    return RelinkReport(
        source_root=source_root,
        library_root=library_root,
        scanned_flacs=len(source_paths),
        target_tracks=len(target_rows),
        already_local=already_local,
        matches=tuple(matches),
        unmatched=tuple(sorted(unmatched, key=lambda item: (item.artist.casefold(), item.title.casefold()))),
        failures=tuple(failures),
        applied=apply,
    )


def format_relink_report(report: RelinkReport) -> str:
    lines = [
        "Library relink",
        f"  Source:          {report.source_root}",
        f"  Canonical:       {report.library_root}",
        f"  FLACs scanned:   {report.scanned_flacs}",
        f"  Target tracks:   {report.target_tracks}",
        f"  Already local:   {report.already_local}",
        f"  Safe matches:    {len(report.matches)}",
        f"  Unmatched:       {len(report.unmatched)}",
        f"  Failures:        {len(report.failures)}",
    ]
    if report.matches:
        lines.append("Matches")
        for match in report.matches:
            lines.append(
                f"  {match.action}: {match.artist} - {match.title} "
                f"[{match.match_method} {match.confidence:.3f}]"
            )
            if match.source_path != match.destination_path:
                lines.append(f"    {match.source_path} -> {match.destination_path}")
            else:
                lines.append(f"    {match.destination_path}")
    if report.unmatched:
        lines.append("Unmatched")
        for item in report.unmatched:
            lines.append(f"  {item.artist} - {item.title}: {item.reason}")
    if report.failures:
        lines.append("Failures")
        for item in report.failures:
            where = f" at {item.path}" if item.path is not None else ""
            lines.append(f"  {item.spotify_id or 'library'}{where}: {item.message}")
    if report.applied:
        lines.append("Relink applied. No source FLACs were deleted.")
    else:
        lines.append("Preview only. Run again with --apply to copy/adopt safe matches.")
    return "\n".join(lines)
