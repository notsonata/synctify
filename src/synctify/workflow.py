from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from typing import Callable

from .acquisition import AcquisitionReport, AcquisitionTask, acquire_tasks, pending_acquisitions
from .auto_resolution import (
    AutoResolutionReport,
    CatalogSearchProvider,
    auto_resolve_tracks,
    format_auto_resolution_report,
)
from .playlists import (
    PlaylistBuildReport,
    build_playlists,
    format_build_report,
    playlists_from_database,
)
from .providers.base import AcquisitionProvider
from .spotify.ingest import SpotifySnapshot
from .spotify.state import ChangePlan, apply_snapshot, format_plan, plan_snapshot


AcquisitionProviderFactory = Callable[[str], AcquisitionProvider]


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
    resolution: AutoResolutionReport
    acquisitions: tuple[AcquisitionGroup, ...]
    playlist_readiness: PlaylistReadiness | None
    playlists: PlaylistBuildReport | None
    dry_run: bool

    @property
    def operational_failures(self) -> int:
        return self.resolution.failed + sum(group.failed for group in self.acquisitions)


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
    resolve_source: str,
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
        resolution = auto_resolve_tracks(
            connection,
            search_provider,
            resolve_source,
            limit=resolution_limit,
            search_results=search_results,
            dry_run=False,
        )
        resolution = replace(resolution, dry_run=True)
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
        resolution=resolution,
        acquisitions=acquisitions,
        playlist_readiness=readiness,
        playlists=None,
        dry_run=True,
    )


def run_update_workflow(
    connection: sqlite3.Connection,
    snapshot: SpotifySnapshot,
    search_provider: CatalogSearchProvider,
    resolve_source: str,
    acquisition_provider_factory: AcquisitionProviderFactory,
    library_dir: Path,
    playlists_dir: Path,
    *,
    search_results: int = 10,
    resolution_limit: int | None = None,
    allow_partial: bool = False,
) -> UpdateWorkflowReport:
    """Apply Spotify state, resolve, acquire, then rebuild playlists."""
    spotify_plan = plan_snapshot(connection, snapshot)
    apply_snapshot(connection, snapshot)
    connection.commit()

    resolution = auto_resolve_tracks(
        connection,
        search_provider,
        resolve_source,
        limit=resolution_limit,
        search_results=search_results,
        dry_run=False,
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
        resolution=resolution,
        acquisitions=acquisitions,
        playlist_readiness=None,
        playlists=playlists,
        dry_run=False,
    )


def format_update_workflow_report(report: UpdateWorkflowReport) -> str:
    sections = [format_plan(report.spotify), format_auto_resolution_report(report.resolution)]

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
