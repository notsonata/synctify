from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Sequence

from .providers.base import AcquisitionProvider
from .resolution import Candidate


@dataclass(slots=True, frozen=True)
class AcquisitionTask:
    spotify_id: str
    provider: str
    provider_track_id: str
    title: str
    artist: str
    album: str | None
    isrc: str | None
    duration_ms: int | None

    def candidate(self) -> Candidate:
        return Candidate(
            provider=self.provider,
            provider_track_id=self.provider_track_id,
            title=self.title,
            artist=self.artist,
            album=self.album,
            isrc=self.isrc,
            duration_ms=self.duration_ms,
        )


@dataclass(slots=True, frozen=True)
class AcquiredResult:
    spotify_id: str
    path: Path
    sha256: str
    reconciled: bool = False


@dataclass(slots=True, frozen=True)
class AcquisitionFailure:
    spotify_id: str
    message: str


@dataclass(slots=True, frozen=True)
class AcquisitionReport:
    completed: tuple[AcquiredResult, ...]
    failures: tuple[AcquisitionFailure, ...]

    @property
    def succeeded(self) -> int:
        return len(self.completed)

    @property
    def reconciled(self) -> int:
        return sum(result.reconciled for result in self.completed)

    @property
    def downloaded(self) -> int:
        return self.succeeded - self.reconciled

    @property
    def failed(self) -> int:
        return len(self.failures)


def pending_acquisitions(
    connection: sqlite3.Connection,
    *,
    provider: str | None = None,
    limit: int | None = None,
) -> tuple[AcquisitionTask, ...]:
    params: list[object] = []
    provider_clause = ""
    if provider is not None:
        provider_clause = " AND r.provider = ?"
        params.append(provider.strip().lower())

    rows = connection.execute(
        f"""
        SELECT
            t.spotify_id,
            r.provider,
            r.provider_track_id,
            t.title,
            t.artist,
            t.album,
            t.isrc,
            t.duration_ms
        FROM track_resolutions AS r
        JOIN tracks AS t ON t.spotify_id = r.spotify_id
        WHERE (t.local_path IS NULL OR t.local_path = '')
          AND EXISTS (
              SELECT 1
              FROM playlist_tracks AS pt
              WHERE pt.track_id = t.spotify_id
          )
        {provider_clause}
        ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
        """,
        params,
    ).fetchall()

    tasks = tuple(
        AcquisitionTask(
            spotify_id=row["spotify_id"],
            provider=row["provider"],
            provider_track_id=row["provider_track_id"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            duration_ms=row["duration_ms"],
        )
        for row in rows
    )
    return tasks if limit is None else tasks[:limit]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_acquired(
    connection: sqlite3.Connection,
    task: AcquisitionTask,
    path: Path,
    sha256: str,
) -> None:
    if task.provider == "qobuz":
        connection.execute(
            """
            UPDATE tracks
            SET qobuz_id = ?, local_path = ?, sha256 = ?, status = 'local'
            WHERE spotify_id = ?
            """,
            (task.provider_track_id, str(path), sha256, task.spotify_id),
        )
    else:
        connection.execute(
            """
            UPDATE tracks
            SET local_path = ?, sha256 = ?, status = 'local'
            WHERE spotify_id = ?
            """,
            (str(path), sha256, task.spotify_id),
        )


def acquire_tasks(
    connection: sqlite3.Connection,
    provider: AcquisitionProvider,
    tasks: Sequence[AcquisitionTask],
    destination: Path,
) -> AcquisitionReport:
    completed: list[AcquiredResult] = []
    failures: list[AcquisitionFailure] = []

    for task in tasks:
        if not provider.supports(task.provider):
            failures.append(
                AcquisitionFailure(
                    task.spotify_id,
                    f"downloader {provider.name!r} does not support source {task.provider!r}",
                )
            )
            continue

        try:
            acquired = provider.acquire(task.candidate(), destination)
            if acquired.provider != task.provider:
                raise ValueError("downloader returned an unexpected source provider")
            if acquired.provider_track_id != task.provider_track_id:
                raise ValueError("downloader returned an unexpected track ID")

            path = acquired.path.expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"acquired file does not exist: {path}")

            sha256 = file_sha256(path)
            _record_acquired(connection, task, path, sha256)
            completed.append(
                AcquiredResult(
                    task.spotify_id,
                    path,
                    sha256,
                    reconciled=acquired.reconciled,
                )
            )
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append(AcquisitionFailure(task.spotify_id, str(exc)))

    return AcquisitionReport(tuple(completed), tuple(failures))


def format_acquisition_plan(tasks: Sequence[AcquisitionTask]) -> str:
    if not tasks:
        return "No resolved tracks are waiting for acquisition."

    lines = [f"Pending acquisitions: {len(tasks)}"]
    for task in tasks:
        lines.append(
            f"  {task.artist} - {task.title} -> {task.provider}:{task.provider_track_id}"
        )
    return "\n".join(lines)
