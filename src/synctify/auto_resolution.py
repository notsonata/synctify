from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Protocol, Sequence

from .models import Track
from .resolution import Candidate, Resolution, ResolutionStatus, resolve_track, save_resolution


class CatalogSearchProvider(Protocol):
    name: str
    supported_sources: frozenset[str]

    def supports(self, source: str) -> bool: ...

    def search(
        self,
        track: Track,
        source: str,
        *,
        limit: int | None = None,
    ) -> Sequence[Candidate]: ...


@dataclass(slots=True, frozen=True)
class AutoResolutionAttempt:
    track: Track
    candidate_count: int
    resolution: Resolution | None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class AutoResolutionReport:
    source: str
    attempts: tuple[AutoResolutionAttempt, ...]
    dry_run: bool

    @property
    def resolved(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.resolution is not None
            and attempt.resolution.status is ResolutionStatus.RESOLVED
        )

    @property
    def ambiguous(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.resolution is not None
            and attempt.resolution.status is ResolutionStatus.AMBIGUOUS
        )

    @property
    def unresolved(self) -> int:
        return sum(
            1
            for attempt in self.attempts
            if attempt.resolution is not None
            and attempt.resolution.status is ResolutionStatus.UNRESOLVED
        )

    @property
    def failed(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.error is not None)


def pending_resolution_tracks(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
    spotify_ids: Sequence[str] | None = None,
) -> tuple[Track, ...]:
    """Return unresolved desired tracks without parameter-per-ID SQL filtering.

    Fallback freezes an initial Spotify-ID set and may pass thousands of IDs back
    through this function for later sources. Filtering that set in Python avoids
    SQLite's connection-specific variable limit while preserving database order.
    """
    selected_ids: set[str] | None = None
    if spotify_ids is not None:
        selected = tuple(dict.fromkeys(spotify_ids))
        if not selected:
            return ()
        selected_ids = set(selected)

    rows = connection.execute(
        """
        SELECT t.spotify_id, t.title, t.artist, t.album, t.isrc, t.duration_ms, t.local_path
        FROM tracks AS t
        WHERE NOT EXISTS (
            SELECT 1
            FROM track_resolutions AS r
            WHERE r.spotify_id = t.spotify_id
        )
          AND EXISTS (
              SELECT 1
              FROM playlist_tracks AS pt
              WHERE pt.track_id = t.spotify_id
          )
        ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
        """
    ).fetchall()
    tracks = tuple(
        Track(
            spotify_id=row["spotify_id"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            duration_ms=row["duration_ms"],
            local_path=None if not row["local_path"] else Path(row["local_path"]),
        )
        for row in rows
        if selected_ids is None or row["spotify_id"] in selected_ids
    )
    return tracks if limit is None else tracks[:limit]


def auto_resolve_tracks(
    connection: sqlite3.Connection,
    search_provider: CatalogSearchProvider,
    source: str,
    *,
    limit: int | None = None,
    search_results: int = 10,
    dry_run: bool = False,
    spotify_ids: Sequence[str] | None = None,
) -> AutoResolutionReport:
    normalized_source = source.strip().lower()
    if not search_provider.supports(normalized_source):
        supported = ", ".join(sorted(search_provider.supported_sources))
        raise ValueError(
            f"search provider {search_provider.name!r} does not support source {normalized_source!r}; supported: {supported}"
        )
    if search_results < 1:
        raise ValueError("search_results must be at least 1")

    attempts: list[AutoResolutionAttempt] = []
    for track in pending_resolution_tracks(
        connection,
        limit=limit,
        spotify_ids=spotify_ids,
    ):
        try:
            candidates = tuple(
                search_provider.search(
                    track,
                    normalized_source,
                    limit=search_results,
                )
            )
            resolution = resolve_track(track, candidates)
            if resolution.status is ResolutionStatus.RESOLVED and not dry_run:
                save_resolution(connection, track.spotify_id, resolution)
            attempts.append(
                AutoResolutionAttempt(
                    track=track,
                    candidate_count=len(candidates),
                    resolution=resolution,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            attempts.append(
                AutoResolutionAttempt(
                    track=track,
                    candidate_count=0,
                    resolution=None,
                    error=str(exc),
                )
            )

    return AutoResolutionReport(normalized_source, tuple(attempts), dry_run)


def format_auto_resolution_report(report: AutoResolutionReport) -> str:
    if not report.attempts:
        return "No unresolved tracks are waiting for automatic resolution."

    lines = [
        f"Automatic resolution source: {report.source}",
        f"Tracks checked: {len(report.attempts)}",
        f"Resolved: {report.resolved}",
        f"Ambiguous: {report.ambiguous}",
        f"Unresolved: {report.unresolved}",
        f"Failed: {report.failed}",
    ]
    for attempt in report.attempts:
        label = f"{attempt.track.artist} - {attempt.track.title}"
        if attempt.error is not None:
            lines.append(f"  ERROR {label}: {attempt.error}")
            continue
        assert attempt.resolution is not None
        resolution = attempt.resolution
        if resolution.status is ResolutionStatus.RESOLVED and resolution.candidate is not None:
            lines.append(
                f"  RESOLVED {label} -> {resolution.candidate.provider}:{resolution.candidate.provider_track_id} "
                f"({resolution.confidence:.3f}, {resolution.method.value if resolution.method else 'unknown'})"
            )
        else:
            lines.append(
                f"  {resolution.status.value.upper()} {label} "
                f"({attempt.candidate_count} candidates, {resolution.confidence:.3f}): {resolution.reason}"
            )
    if report.dry_run:
        lines.append("Dry run only. No source resolutions were saved.")
    return "\n".join(lines)
