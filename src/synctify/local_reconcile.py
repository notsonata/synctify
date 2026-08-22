from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .acquisition import file_sha256
from .resolution import Candidate
from .providers.reconcile import FLACReconciliationIndex


@dataclass(slots=True, frozen=True)
class LocalReconcileReport:
    desired: int
    reused: int
    matched: int
    stale_cleared: int

    @property
    def available(self) -> int:
        return self.reused + self.matched


def _usable_recorded_flac(raw_path: str | None, library_dir: Path) -> Path | None:
    if not raw_path:
        return None
    root = library_dir.expanduser().resolve()
    try:
        path = Path(raw_path).expanduser().resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() and path.suffix.lower() == ".flac" else None


def reconcile_confirmed_local_tracks(
    connection: sqlite3.Connection,
    library_dir: Path,
) -> LocalReconcileReport:
    """Attach confirmed desired tracks to safe existing FLACs before provider lookup.

    Exact Spotify identities keep their previously recorded ``local_path`` when a
    reviewed playlist is materialized. For confirmed tracks without a usable
    recorded path, scan the canonical library once and use the same conservative
    ISRC/metadata matcher used by acquisition reconciliation.
    """
    rows = connection.execute(
        """
        SELECT DISTINCT
            t.spotify_id, t.title, t.artist, t.album, t.isrc, t.duration_ms,
            t.local_path
        FROM tracks AS t
        JOIN playlist_tracks AS pt ON pt.track_id = t.spotify_id
        ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
        """
    ).fetchall()

    reused = 0
    stale_cleared = 0
    missing: list[sqlite3.Row] = []
    for row in rows:
        recorded = _usable_recorded_flac(row["local_path"], library_dir)
        if recorded is not None:
            reused += 1
            continue
        if row["local_path"]:
            connection.execute(
                "UPDATE tracks SET local_path = NULL, sha256 = NULL, status = 'unresolved' WHERE spotify_id = ?",
                (row["spotify_id"],),
            )
            stale_cleared += 1
        missing.append(row)

    if not missing:
        return LocalReconcileReport(len(rows), reused, 0, stale_cleared)

    index = FLACReconciliationIndex(library_dir)
    matched = 0
    for row in missing:
        candidate = Candidate(
            provider="local",
            provider_track_id=str(row["spotify_id"]),
            title=str(row["title"]),
            artist=str(row["artist"]),
            album=row["album"],
            isrc=row["isrc"],
            duration_ms=row["duration_ms"],
        )
        path = index.find(candidate)
        if path is None:
            continue
        connection.execute(
            """
            UPDATE tracks
            SET local_path = ?, sha256 = ?, status = 'local'
            WHERE spotify_id = ?
            """,
            (str(path), file_sha256(path), row["spotify_id"]),
        )
        matched += 1

    return LocalReconcileReport(len(rows), reused, matched, stale_cleared)
