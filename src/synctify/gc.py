from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable


@dataclass(slots=True, frozen=True)
class CollectibleTrack:
    spotify_id: str
    title: str
    artist: str
    path: Path
    size_bytes: int | None
    exists: bool
    safe_to_delete: bool
    reason: str | None = None


@dataclass(slots=True, frozen=True)
class CleanupFailure:
    spotify_id: str
    path: Path
    message: str


@dataclass(slots=True, frozen=True)
class CleanupReport:
    candidates: tuple[CollectibleTrack, ...]
    deleted: tuple[CollectibleTrack, ...]
    cleared_missing: tuple[CollectibleTrack, ...]
    skipped: tuple[CollectibleTrack, ...]
    failures: tuple[CleanupFailure, ...]
    dry_run: bool

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.size_bytes or 0 for item in self.candidates if item.safe_to_delete)

    @property
    def deleted_bytes(self) -> int:
        return sum(item.size_bytes or 0 for item in self.deleted)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _track_candidate(row: sqlite3.Row, library_dir: Path) -> CollectibleTrack:
    recorded = Path(row["local_path"]).expanduser()
    root = library_dir.expanduser().resolve()

    if recorded.is_symlink():
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=recorded,
            size_bytes=None,
            exists=True,
            safe_to_delete=False,
            reason=(
                "recorded local path is a symbolic link; refusing to resolve or delete through it "
                "inside or outside the canonical library"
            ),
        )

    try:
        resolved = recorded.resolve(strict=False)
    except OSError as exc:
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=recorded,
            size_bytes=None,
            exists=False,
            safe_to_delete=False,
            reason=f"could not resolve path: {exc}",
        )

    if row["local_path_users"] > 1:
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=resolved,
            size_bytes=None,
            exists=resolved.exists(),
            safe_to_delete=False,
            reason="local path is shared by multiple track records",
        )

    if not _is_within(resolved, root):
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=resolved,
            size_bytes=None,
            exists=resolved.exists(),
            safe_to_delete=False,
            reason="recorded path is outside the canonical library",
        )

    if not resolved.exists():
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=resolved,
            size_bytes=None,
            exists=False,
            safe_to_delete=True,
            reason="recorded local file is already missing",
        )

    if not resolved.is_file():
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=resolved,
            size_bytes=None,
            exists=True,
            safe_to_delete=False,
            reason="recorded local path is not a regular file",
        )

    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return CollectibleTrack(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            path=resolved,
            size_bytes=None,
            exists=True,
            safe_to_delete=False,
            reason=f"could not stat file: {exc}",
        )

    return CollectibleTrack(
        spotify_id=row["spotify_id"],
        title=row["title"],
        artist=row["artist"],
        path=resolved,
        size_bytes=size,
        exists=True,
        safe_to_delete=True,
    )


def collectible_tracks(
    connection: sqlite3.Connection,
    library_dir: Path,
    *,
    spotify_ids: Iterable[str] | None = None,
) -> tuple[CollectibleTrack, ...]:
    """Return local tracks with zero references from the current desired playlist state.

    When spotify_ids is supplied, only those track records are considered. This is
    used by playlist unimport so it cannot opportunistically delete unrelated
    unreferenced files elsewhere in the library.
    """
    selected = tuple(dict.fromkeys(spotify_ids or ()))
    selected_clause = ""
    params: tuple[str, ...] = ()
    if spotify_ids is not None:
        if not selected:
            return ()
        placeholders = ",".join("?" for _ in selected)
        selected_clause = f"AND t.spotify_id IN ({placeholders})"
        params = selected

    rows = connection.execute(
        f"""
        SELECT
            t.spotify_id,
            t.title,
            t.artist,
            t.local_path,
            (
                SELECT COUNT(*)
                FROM tracks AS other
                WHERE other.local_path = t.local_path
            ) AS local_path_users
        FROM tracks AS t
        WHERE t.local_path IS NOT NULL
          AND t.local_path != ''
          {selected_clause}
          AND NOT EXISTS (
              SELECT 1
              FROM playlist_tracks AS pt
              WHERE pt.track_id = t.spotify_id
          )
        ORDER BY t.artist COLLATE NOCASE, t.title COLLATE NOCASE
        """,
        params,
    ).fetchall()
    return tuple(_track_candidate(row, library_dir) for row in rows)


def _clear_local_state(connection: sqlite3.Connection, spotify_id: str) -> None:
    has_resolution = connection.execute(
        "SELECT 1 FROM track_resolutions WHERE spotify_id = ?",
        (spotify_id,),
    ).fetchone() is not None
    connection.execute(
        """
        UPDATE tracks
        SET local_path = NULL,
            sha256 = NULL,
            status = ?
        WHERE spotify_id = ?
        """,
        ("resolved" if has_resolution else "unresolved", spotify_id),
    )


def _prune_empty_parents(path: Path, library_dir: Path) -> None:
    root = library_dir.expanduser().resolve()
    current = path.parent
    while current != root and _is_within(current, root):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def clean_unreferenced_tracks(
    connection: sqlite3.Connection,
    library_dir: Path,
    *,
    apply: bool = False,
    spotify_ids: Iterable[str] | None = None,
) -> CleanupReport:
    candidates = collectible_tracks(
        connection,
        library_dir,
        spotify_ids=spotify_ids,
    )
    if not apply:
        skipped = tuple(item for item in candidates if not item.safe_to_delete)
        return CleanupReport(candidates, (), (), skipped, (), True)

    deleted: list[CollectibleTrack] = []
    cleared_missing: list[CollectibleTrack] = []
    skipped: list[CollectibleTrack] = []
    failures: list[CleanupFailure] = []

    for item in candidates:
        if not item.safe_to_delete:
            skipped.append(item)
            continue

        if not item.exists:
            _clear_local_state(connection, item.spotify_id)
            cleared_missing.append(item)
            continue

        try:
            item.path.unlink()
            _clear_local_state(connection, item.spotify_id)
            _prune_empty_parents(item.path, library_dir)
            deleted.append(item)
        except OSError as exc:
            failures.append(CleanupFailure(item.spotify_id, item.path, str(exc)))

    return CleanupReport(
        candidates,
        tuple(deleted),
        tuple(cleared_missing),
        tuple(skipped),
        tuple(failures),
        False,
    )


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def format_cleanup_report(report: CleanupReport) -> str:
    if not report.candidates:
        return "No unreferenced local tracks found."

    lines = [
        f"Unreferenced local tracks: {len(report.candidates)}",
        f"Reclaimable: {format_bytes(report.reclaimable_bytes)}",
    ]
    for item in report.candidates:
        size = format_bytes(item.size_bytes) if item.size_bytes is not None else "unknown size"
        state = "safe"
        if not item.safe_to_delete:
            state = f"SKIP: {item.reason}"
        elif not item.exists:
            state = "missing file; local state can be cleared"
        lines.append(f"  {item.artist} - {item.title} [{size}] ({state})")
        lines.append(f"    {item.path}")

    if report.dry_run:
        lines.append("Preview only. Run `synctify clean --apply` to delete safe candidates.")
    else:
        lines.append(f"Deleted: {len(report.deleted)} ({format_bytes(report.deleted_bytes)})")
        lines.append(f"Missing-state cleared: {len(report.cleared_missing)}")
        lines.append(f"Skipped: {len(report.skipped)}")
        lines.append(f"Failed: {len(report.failures)}")
    return "\n".join(lines)
