from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .acquisition import file_sha256
from .providers.reconcile import read_flac_candidate
from .resolution import Candidate, normalize_isrc, normalize_text


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


def _safe_library_candidates(library_dir: Path) -> dict[Path, Candidate]:
    root = library_dir.expanduser().resolve()
    if not root.exists():
        return {}

    candidates: dict[Path, Candidate] = {}
    for raw_path in root.rglob("*"):
        try:
            path = raw_path.resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            continue
        if not path.is_file() or path.suffix.lower() != ".flac":
            continue
        candidate = read_flac_candidate(path)
        if candidate is not None:
            candidates[path] = candidate
    return candidates


def _recorded_identity_matches(row: sqlite3.Row, candidate: Candidate) -> bool:
    """Require identity-grade agreement before trusting an existing local_path."""
    spotify_isrc = normalize_isrc(row["isrc"])
    candidate_isrc = normalize_isrc(candidate.isrc)
    if spotify_isrc and candidate_isrc:
        return spotify_isrc == candidate_isrc

    if normalize_text(row["title"]) != normalize_text(candidate.title):
        return False
    if normalize_text(row["artist"]) != normalize_text(candidate.artist):
        return False

    spotify_duration = row["duration_ms"]
    candidate_duration = candidate.duration_ms
    if spotify_duration is not None and candidate_duration is not None:
        if abs(int(spotify_duration) - int(candidate_duration)) > 2_000:
            return False
    return True


def reconcile_confirmed_local_tracks(
    connection: sqlite3.Connection,
    library_dir: Path,
) -> LocalReconcileReport:
    """Attach confirmed desired tracks to safe existing FLACs before provider lookup.

    Recorded paths are reused only after the FLAC metadata agrees with the Spotify
    identity. Unrecorded files are adopted automatically only for a unique exact
    ISRC match. Metadata-only fuzzy matching is intentionally excluded from this
    automatic path because an incorrect reuse is worse than a missing download.
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

    if not rows:
        return LocalReconcileReport(0, 0, 0, 0)

    candidates = _safe_library_candidates(library_dir)
    claimed_paths: set[Path] = set()
    reused = 0
    stale_cleared = 0
    missing: list[sqlite3.Row] = []

    for row in rows:
        recorded = _usable_recorded_flac(row["local_path"], library_dir)
        candidate = None if recorded is None else candidates.get(recorded)
        if (
            recorded is not None
            and candidate is not None
            and recorded not in claimed_paths
            and _recorded_identity_matches(row, candidate)
        ):
            claimed_paths.add(recorded)
            reused += 1
            continue

        if row["local_path"]:
            connection.execute(
                """
                UPDATE tracks
                SET local_path = NULL, sha256 = NULL, status = 'unresolved'
                WHERE spotify_id = ?
                """,
                (row["spotify_id"],),
            )
            stale_cleared += 1
        missing.append(row)

    by_isrc: dict[str, list[Path]] = {}
    for path, candidate in candidates.items():
        if path in claimed_paths:
            continue
        normalized = normalize_isrc(candidate.isrc)
        if normalized:
            by_isrc.setdefault(normalized, []).append(path)

    matched = 0
    for row in missing:
        spotify_isrc = normalize_isrc(row["isrc"])
        if not spotify_isrc:
            continue
        available = [path for path in by_isrc.get(spotify_isrc, ()) if path not in claimed_paths]
        if len(available) != 1:
            continue
        path = available[0]
        claimed_paths.add(path)
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
