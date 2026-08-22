from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from typing import Callable, Sequence

from .acquisition import AcquisitionReport, AcquisitionTask, acquire_tasks, pending_acquisitions
from .auto_resolution import (
    AutoResolutionReport,
    CatalogSearchProvider,
    auto_resolve_tracks,
    format_auto_resolution_report,
    pending_resolution_tracks,
)
from .playlists import (
    PlaylistBuildReport,
    build_playlists,
    format_build_report,
    playlists_from_database,
)
from .providers.base import AcquisitionProvider
from .resolution import ResolutionStatus
from .resolution_policy import clear_unsupported_resolutions
from .spotify.ingest import SpotifySnapshot
from .spotify.state import ChangePlan, apply_snapshot, format_plan, plan_snapshot


AcquisitionProviderFactory = Callable[[str], AcquisitionProvider]
DEFAULT_SOURCE_PRIORITY = ("qobuz", "tidal", "deezer")


@dataclass(slots=True, frozen=True)
class PlaylistReadiness:
    playlists: int
    complete: int
    incomplete: int
    missing_tracks: int


@dataclass(slots=True, frozen=True)
class AcquisitionGroup:
    source: str
    downloader: str | None
    tasks: tuple[AcquisitionTask, ...]
    report: AcquisitionReport | None = None
    error: str | None = None

    @property
    def planned(self) -> int:
        return len(self.tasks)

    @property
    def succeeded(self) -> int:
        return 0 if self.report is None else self.report.succeeded

    @property
    def failed(self) -> int:
        if self.error is not None:
            return len(self.tasks)
        return 0 if self.report is None else self.report.failed


@dataclass(slots=True, frozen=True)
class UpdateWorkflowReport:
    spotify: ChangePlan
    resolution_sources: tuple[str, ...]
    resolutions: tuple[AutoResolutionReport, ...]
    acquisitions: tuple[AcquisitionGroup, ...]
    playlist_readiness: PlaylistReadiness | None
    playlists: PlaylistBuildReport | None
    dry_run: bool
    allow_partial: bool = False

    @property
    def resolution(self) -> AutoResolutionReport:
        """Compatibility view containing each track's final fallback outcome."""
        if not self.resolutions:
            source = self.resolution_sources[0] if self.resolution_sources else "none"
            return AutoResolutionReport(source, (), self.dry_run)
        if len(self.resolutions) == 1:
            return self.resolutions[0]

        order: list[str] = []
        final_attempts = {}
        for report in self.resolutions:
            for attempt in report.attempts:
                spotify_id = attempt.track.spotify_id
                if spotify_id not in final_attempts:
                    order.append(spotify_id)
                final_attempts[spotify_id] = attempt
        source = " -> ".join(self.resolution_sources)
        return AutoResolutionReport(
            source,
            tuple(final_attempts[spotify_id] for spotify_id in order),
            self.dry_run,
        )

    @property
    def resolution_failures(self) -> int:
        """Count only search errors that were not recovered by a later source."""
        failed_ids = {
            attempt.track.spotify_id
            for report in self.resolutions
            for attempt in report.attempts
            if attempt.error is not None
        }
        resolved_ids = {
            attempt.track.spotify_id
            for report in self.resolutions
            for attempt in report.attempts
            if attempt.resolution is not None
            and attempt.resolution.status is ResolutionStatus.RESOLVED
        }
        return len(failed_ids - resolved_ids)

    @property
    def strict_playlist_failures(self) -> int:
        if self.dry_run or self.allow_partial or self.playlists is None:
            return 0
        return self.playlists.incomplete

    @property
    def operational_failures(self) -> int:
        primary_failures = self.resolution_failures + sum(
            group.failed for group in self.acquisitions
        )
        # An acquisition/search failure commonly causes the same playlist to be
        # incomplete. Preserve the primary failure count rather than double-counting
        # that cascade, while still making a purely incomplete strict rebuild fail.
        return primary_failures or self.strict_playlist_failures


def normalize_source_priority(sources: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(sources, str):
        raw_sources = (sources,)
    else:
        raw_sources = tuple(sources)
    normalized = tuple(
        dict.fromkeys(source.strip().lower() for source in raw_sources if source.strip())
    )
    if not normalized:
        raise ValueError("at least one automatic-resolution source is required")
    return normalized


def playlist_readiness(connection: sqlite3.Connection) -> PlaylistReadiness:
    playlists = playlists_from_database(connection)
    complete = 0
    missing_tracks = 0
    for playlist in playlists:
        missing = sum(
            track.local_path is None or not track.local_path.is_file()
            for track in playlist.tracks
        )
        if missing:
            missing_tracks += missing
        else:
            complete += 1
    return PlaylistReadiness(
        playlists=len(playlists),
        complete=complete,
        incomplete=len(playlists) - complete,
        missing_tracks=missing_tracks,
    )


def _run_resolution_priority(
    connection: sqlite3.Connection,
    search_provider: CatalogSearchProvider,
    sources: str | Sequence[str],
    *,
    search_results: int,
    resolution_limit: int | None,
    preview: bool,
) -> tuple[tuple[str, ...], tuple[AutoResolutionReport, ...]]:
    priority = normalize_source_priority(sources)
    for source in priority:
        if not search_provider.supports(source):
            supported = ", ".join(sorted(search_provider.supported_sources))
            raise ValueError(
                f"automatic-resolution source {source!r} is unsupported; supported: {supported}"
            )

    # Releases before the canonical-lossless policy could persist SoundCloud
    # resolutions. Remove those obsolete rows before selecting unresolved tracks;
    # preview callers run inside a savepoint, so this migration is rolled back there.
    clear_unsupported_resolutions(connection)

    selected_tracks = pending_resolution_tracks(connection, limit=resolution_limit)
    selected_ids = tuple(track.spotify_id for track in selected_tracks)
    if not selected_ids:
        return priority, ()

    reports: list[AutoResolutionReport] = []
    for source in priority:
        if not pending_resolution_tracks(connection, spotify_ids=selected_ids):
            break
        report = auto_resolve_tracks(
            connection,
            search_provider,
            source,
            search_results=search_results,
            dry_run=False,
            spotify_ids=selected_ids,
        )
        if preview:
            report = replace(report, dry_run=True)
        reports.append(report)
    return priority, tuple(reports)


def _group_pending_acquisitions(
    connection: sqlite3.Connection,
    provider_factory: AcquisitionProviderFactory,
) -> tuple[AcquisitionGroup, ...]:
    grouped: dict[str, list[AcquisitionTask]] = {}
    for task in pending_acquisitions(connection):
        grouped.setdefault(task.provider, []).append(task)

    groups: list[AcquisitionGroup] = []
    for source in sorted(grouped):
        tasks = tuple(grouped[source])
        try:
            provider = provider_factory(source)
        except (RuntimeError, ValueError) as exc:
            groups.append(
                AcquisitionGroup(
                    source=source,
                    downloader=None,
                    tasks=tasks,
                    error=str(exc),
                )
            )
            continue
        groups.append(
            AcquisitionGroup(
                source=source,
                downloader=provider.name,
                tasks=tasks,
            )
        )
    return tuple(groups)


def _run_acquisitions(
    connection: sqlite3.Connection,
    groups: tuple[AcquisitionGroup, ...],
    provider_factory: AcquisitionProviderFactory,
    library_dir: Path,
) -> tuple[AcquisitionGroup, ...]:
    completed: list[AcquisitionGroup] = []
    for group in groups:
        if group.error is not None:
            completed.append(group)
            continue
        try:
            provider = provider_factory(group.source)
            report = acquire_tasks(connection, provider, group.tasks, library_dir)
            connection.commit()
            completed.append(replace(group, downloader=provider.name, report=report))
        except (OSError, RuntimeError, ValueError) as exc:
            connection.rollback()
            completed.append(replace(group, error=str(exc)))
    return tuple(completed)


def preview_update_workflow(
    connection: sqlite3.Connection,
    snapshot: SpotifySnapshot,
    search_provider: CatalogSearchProvider,
    resolve_sources: str | Sequence[str],
    acquisition_provider_factory: AcquisitionProviderFactory,
    *,
    search_results: int = 10,
    resolution_limit: int | None = None,
) -> UpdateWorkflowReport:
    """Preview the post-pull workflow against a savepoint and roll it back."""
    spotify_plan = plan_snapshot(connection, snapshot)
    connection.execute("SAVEPOINT synctify_update_preview")
    try:
        apply_snapshot(connection, snapshot)
        resolution_sources, resolutions = _run_resolution_priority(
            connection,
            search_provider,
            resolve_sources,
            search_results=search_results,
            resolution_limit=resolution_limit,
            preview=True,
        )
        acquisitions = _group_pending_acquisitions(
            connection,
            acquisition_provider_factory,
        )
        readiness = playlist_readiness(connection)
    finally:
        connection.execute("ROLLBACK TO SAVEPOINT synctify_update_preview")
        connection.execute("RELEASE SAVEPOINT synctify_update_preview")

    return UpdateWorkflowReport(
        spotify=spotify_plan,
        resolution_sources=resolution_sources,
        resolutions=resolutions,
        acquisitions=acquisitions,
        playlist_readiness=readiness,
        playlists=None,
        dry_run=True,
    )


def run_update_workflow(
    connection: sqlite3.Connection,
    snapshot: SpotifySnapshot,
    search_provider: CatalogSearchProvider,
    resolve_sources: str | Sequence[str],
    acquisition_provider_factory: AcquisitionProviderFactory,
    library_dir: Path,
    playlists_dir: Path,
    *,
    search_results: int = 10,
    resolution_limit: int | None = None,
    allow_partial: bool = False,
) -> UpdateWorkflowReport:
    """Apply Spotify state, resolve with ordered fallback, acquire, then rebuild playlists."""
    spotify_plan = plan_snapshot(connection, snapshot)
    apply_snapshot(connection, snapshot)
    connection.commit()

    resolution_sources, resolutions = _run_resolution_priority(
        connection,
        search_provider,
        resolve_sources,
        search_results=search_results,
        resolution_limit=resolution_limit,
        preview=False,
    )
    connection.commit()

    planned_acquisitions = _group_pending_acquisitions(
        connection,
        acquisition_provider_factory,
    )
    acquisitions = _run_acquisitions(
        connection,
        planned_acquisitions,
        acquisition_provider_factory,
        library_dir,
    )

    playlists = build_playlists(
        connection,
        playlists_dir,
        allow_partial=allow_partial,
    )
    return UpdateWorkflowReport(
        spotify=spotify_plan,
        resolution_sources=resolution_sources,
        resolutions=resolutions,
        acquisitions=acquisitions,
        playlist_readiness=None,
        playlists=playlists,
        dry_run=False,
        allow_partial=allow_partial,
    )


def format_update_workflow_report(report: UpdateWorkflowReport) -> str:
    sections = [
        format_plan(report.spotify),
        "Automatic resolution priority\n  " + " -> ".join(report.resolution_sources),
    ]

    if not report.resolutions:
        sections.append("No unresolved tracks are waiting for automatic resolution.")
    else:
        sections.extend(format_auto_resolution_report(item) for item in report.resolutions)

    acquisition_lines = ["Acquisition"]
    if not report.acquisitions:
        acquisition_lines.append("  No resolved tracks are waiting for download.")
    for group in report.acquisitions:
        downloader = group.downloader or "unavailable"
        if report.dry_run:
            suffix = f"ERROR: {group.error}" if group.error else "planned"
            acquisition_lines.append(
                f"  {group.source} via {downloader}: {group.planned} track(s), {suffix}"
            )
            continue
        acquisition_lines.append(
            f"  {group.source} via {downloader}: planned {group.planned}, "
            f"acquired {group.succeeded}, failed {group.failed}"
        )
        if group.error:
            acquisition_lines.append(f"    ERROR: {group.error}")
        elif group.report is not None:
            for failure in group.report.failures:
                acquisition_lines.append(
                    f"    {failure.spotify_id}: {failure.message}"
                )
    sections.append("\n".join(acquisition_lines))

    if report.dry_run:
        readiness = report.playlist_readiness
        assert readiness is not None
        sections.append(
            "Playlist readiness before planned downloads\n"
            f"  Complete:   {readiness.complete}/{readiness.playlists}\n"
            f"  Incomplete: {readiness.incomplete}\n"
            f"  Missing track references: {readiness.missing_tracks}\n"
            "Preview only. Spotify/resolution state was rolled back and no audio or playlists were written."
        )
    else:
        assert report.playlists is not None
        sections.append(format_build_report(report.playlists))

    return "\n\n".join(sections)
