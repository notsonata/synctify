from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import sqlite3

from .acquisition import file_sha256
from .models import Track
from .providers.reconcile import read_flac_candidate
from .resolution import ResolutionStatus, resolve_track


class AuditIssueKind(StrEnum):
    MISSING = "missing"
    HASH_MISSING = "hash-missing"
    HASH_MISMATCH = "hash-mismatch"
    OUTSIDE_LIBRARY = "outside-library"
    NOT_FILE = "not-file"


@dataclass(slots=True, frozen=True)
class RecordedIssue:
    spotify_id: str
    title: str
    artist: str
    kind: AuditIssueKind
    path: Path
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    message: str | None = None


@dataclass(slots=True, frozen=True)
class Adoption:
    spotify_id: str
    title: str
    artist: str
    path: Path


@dataclass(slots=True, frozen=True)
class AuditRepair:
    action: str
    spotify_id: str
    path: Path | None = None


@dataclass(slots=True, frozen=True)
class AuditFailure:
    spotify_id: str | None
    path: Path | None
    message: str


@dataclass(slots=True, frozen=True)
class LibraryAuditReport:
    issues: tuple[RecordedIssue, ...]
    untracked_files: tuple[Path, ...]
    proposed_adoptions: tuple[Adoption, ...]
    repairs: tuple[AuditRepair, ...]
    failures: tuple[AuditFailure, ...]
    repaired: bool

    @property
    def missing(self) -> int:
        return sum(issue.kind is AuditIssueKind.MISSING for issue in self.issues)

    @property
    def hash_problems(self) -> int:
        return sum(
            issue.kind in {AuditIssueKind.HASH_MISSING, AuditIssueKind.HASH_MISMATCH}
            for issue in self.issues
        )

    @property
    def unsafe_paths(self) -> int:
        return sum(
            issue.kind in {AuditIssueKind.OUTSIDE_LIBRARY, AuditIssueKind.NOT_FILE}
            for issue in self.issues
        )


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


def _track_rows(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
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
                r.provider_track_id AS resolution_track_id,
                EXISTS (
                    SELECT 1
                    FROM playlist_tracks AS pt
                    WHERE pt.track_id = t.spotify_id
                ) AS referenced
            FROM tracks AS t
            LEFT JOIN track_resolutions AS r ON r.spotify_id = t.spotify_id
            ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
            """
        ).fetchall()
    )


def _safe_library_flacs(root: Path) -> tuple[Path, ...]:
    if not root.exists() or not root.is_dir():
        return ()
    safe: set[Path] = set()
    for path in root.rglob("*"):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if (
            _within(resolved, root)
            and resolved.is_file()
            and resolved.suffix.lower() == ".flac"
        ):
            safe.add(resolved)
    return tuple(sorted(safe))


def _clear_local_state(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    status = "resolved" if row["resolution_provider"] is not None else "unresolved"
    connection.execute(
        """
        UPDATE tracks
        SET local_path = NULL, sha256 = NULL, status = ?
        WHERE spotify_id = ?
        """,
        (status, row["spotify_id"]),
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


def _metadata_matches(track: Track, path: Path) -> bool:
    candidate = read_flac_candidate(path)
    if candidate is None:
        return False
    resolution = resolve_track(track, (candidate,))
    return resolution.status is ResolutionStatus.RESOLVED


def audit_library(
    connection: sqlite3.Connection,
    library_dir: Path,
    *,
    repair: bool = False,
) -> LibraryAuditReport:
    root = library_dir.expanduser().resolve()
    rows = _track_rows(connection)
    by_id = {row["spotify_id"]: row for row in rows}
    issues: list[RecordedIssue] = []
    failures: list[AuditFailure] = []
    recorded_existing: set[Path] = set()
    missing_ids: set[str] = set()
    target_ids: set[str] = set()

    for row in rows:
        raw_path = row["local_path"]
        if not raw_path:
            if row["referenced"]:
                target_ids.add(row["spotify_id"])
            continue

        recorded = Path(raw_path).expanduser()
        try:
            path = recorded.resolve(strict=False)
        except OSError as exc:
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.OUTSIDE_LIBRARY,
                    recorded,
                    message=f"could not resolve recorded path: {exc}",
                )
            )
            continue

        if not _within(path, root):
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.OUTSIDE_LIBRARY,
                    path,
                    message="recorded path is outside the canonical library",
                )
            )
            continue

        if not path.exists():
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.MISSING,
                    path,
                    expected_sha256=row["sha256"],
                    message="recorded local file is missing",
                )
            )
            missing_ids.add(row["spotify_id"])
            if row["referenced"]:
                target_ids.add(row["spotify_id"])
            continue

        if not path.is_file():
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.NOT_FILE,
                    path,
                    message="recorded local path is not a regular file",
                )
            )
            continue

        recorded_existing.add(path)
        try:
            actual_sha = file_sha256(path)
        except OSError as exc:
            failures.append(AuditFailure(row["spotify_id"], path, str(exc)))
            continue

        if not row["sha256"]:
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.HASH_MISSING,
                    path,
                    actual_sha256=actual_sha,
                    message="recorded file has no stored SHA-256",
                )
            )
        elif row["sha256"] != actual_sha:
            issues.append(
                RecordedIssue(
                    row["spotify_id"],
                    row["title"],
                    row["artist"],
                    AuditIssueKind.HASH_MISMATCH,
                    path,
                    expected_sha256=row["sha256"],
                    actual_sha256=actual_sha,
                    message="recorded SHA-256 does not match the local file",
                )
            )

    all_flacs = _safe_library_flacs(root)
    untracked = tuple(path for path in all_flacs if path not in recorded_existing)
    local_candidates = tuple(
        candidate
        for path in untracked
        if (candidate := read_flac_candidate(path)) is not None
    )

    proposed_by_track: dict[str, Path] = {}
    proposed_users: dict[Path, list[str]] = {}
    for spotify_id in sorted(target_ids):
        row = by_id[spotify_id]
        resolution = resolve_track(_track_from_row(row), local_candidates)
        if resolution.status is not ResolutionStatus.RESOLVED or resolution.candidate is None:
            continue
        path = Path(resolution.candidate.provider_track_id).resolve()
        if not _within(path, root) or path not in untracked:
            continue
        proposed_by_track[spotify_id] = path
        proposed_users.setdefault(path, []).append(spotify_id)

    adoptions = tuple(
        Adoption(
            spotify_id,
            by_id[spotify_id]["title"],
            by_id[spotify_id]["artist"],
            path,
        )
        for spotify_id, path in sorted(proposed_by_track.items())
        if len(proposed_users[path]) == 1
    )
    adoption_by_track = {item.spotify_id: item for item in adoptions}

    repairs: list[AuditRepair] = []
    if repair:
        for adoption in adoptions:
            row = by_id[adoption.spotify_id]
            try:
                sha256 = file_sha256(adoption.path)
                _record_local_file(connection, row, adoption.path, sha256)
                repairs.append(AuditRepair("relinked", adoption.spotify_id, adoption.path))
            except OSError as exc:
                failures.append(AuditFailure(adoption.spotify_id, adoption.path, str(exc)))

        for spotify_id in sorted(missing_ids):
            if spotify_id in adoption_by_track:
                continue
            row = by_id[spotify_id]
            _clear_local_state(connection, row)
            repairs.append(AuditRepair("cleared-missing", spotify_id))

        for issue in issues:
            if issue.kind not in {AuditIssueKind.HASH_MISSING, AuditIssueKind.HASH_MISMATCH}:
                continue
            row = by_id[issue.spotify_id]
            if issue.actual_sha256 is None:
                continue
            if not _metadata_matches(_track_from_row(row), issue.path):
                continue
            connection.execute(
                "UPDATE tracks SET sha256 = ?, status = 'local' WHERE spotify_id = ?",
                (issue.actual_sha256, issue.spotify_id),
            )
            repairs.append(AuditRepair("hash-updated", issue.spotify_id, issue.path))

    return LibraryAuditReport(
        issues=tuple(issues),
        untracked_files=untracked,
        proposed_adoptions=adoptions,
        repairs=tuple(repairs),
        failures=tuple(failures),
        repaired=repair,
    )


def format_audit_report(report: LibraryAuditReport) -> str:
    lines = [
        "Library audit",
        f"  Recorded issues: {len(report.issues)}",
        f"  Missing files:   {report.missing}",
        f"  Hash problems:   {report.hash_problems}",
        f"  Unsafe paths:    {report.unsafe_paths}",
        f"  Untracked FLACs: {len(report.untracked_files)}",
        f"  Safe adoptions:  {len(report.proposed_adoptions)}",
    ]

    for issue in report.issues:
        label = f"{issue.artist} - {issue.title}"
        lines.append(f"  {issue.kind.value.upper()} {label}")
        lines.append(f"    {issue.path}")
        if issue.message:
            lines.append(f"    {issue.message}")

    if report.untracked_files:
        lines.append("Untracked canonical FLACs")
        adoption_paths = {item.path: item for item in report.proposed_adoptions}
        for path in report.untracked_files:
            adoption = adoption_paths.get(path)
            if adoption is None:
                lines.append(f"  {path}")
            else:
                lines.append(
                    f"  {path} -> {adoption.artist} - {adoption.title} ({adoption.spotify_id})"
                )

    if report.repaired:
        lines.append(f"Repairs applied: {len(report.repairs)}")
        for item in report.repairs:
            suffix = f" -> {item.path}" if item.path is not None else ""
            lines.append(f"  {item.action}: {item.spotify_id}{suffix}")
    else:
        lines.append("Preview only. Run `synctify audit --repair` to apply safe database repairs.")

    if report.failures:
        lines.append(f"Failures: {len(report.failures)}")
        for failure in report.failures:
            where = f" at {failure.path}" if failure.path is not None else ""
            who = failure.spotify_id or "library"
            lines.append(f"  {who}{where}: {failure.message}")

    return "\n".join(lines)
