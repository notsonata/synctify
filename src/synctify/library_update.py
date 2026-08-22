from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Sequence

from .auto_resolution import CatalogSearchProvider
from .local_reconcile import reconcile_confirmed_local_tracks
from .spotify.state import ChangePlan
from .workflow import (
    AcquisitionProviderFactory,
    ProgressReporter,
    UpdateWorkflowReport,
    _group_pending_acquisitions,
    _run_acquisitions,
    _run_resolution_priority,
    _stderr_progress,
    _notify,
    playlist_readiness,
)
from .playlists import build_playlists


def _unchanged_spotify_plan() -> ChangePlan:
    return ChangePlan((), (), (), (), 0, 0, 0, ())


def _reconcile_local_library(
    connection: sqlite3.Connection,
    library_dir: Path,
    progress: ProgressReporter | None,
) -> None:
    _notify(progress, "Matching confirmed tracks against the local FLAC library...")
    report = reconcile_confirmed_local_tracks(connection, library_dir)
    if report.desired == 0:
        _notify(progress, "No confirmed tracks are waiting for local matching.")
        return
    _notify(
        progress,
        "Local FLAC match: "
        f"{report.reused} recorded path(s) reused, "
        f"{report.matched} existing FLAC(s) matched, "
        f"{report.stale_cleared} stale path(s) cleared.",
    )


def preview_library_update_workflow(
    connection: sqlite3.Connection,
    search_provider: CatalogSearchProvider,
    resolve_sources: str | Sequence[str],
    acquisition_provider_factory: AcquisitionProviderFactory,
    library_dir: Path,
    *,
    search_results: int = 10,
    resolution_limit: int | None = None,
    progress: ProgressReporter | None = _stderr_progress,
) -> UpdateWorkflowReport:
    """Preview resolution/acquisition for the already-confirmed desired library."""
    _notify(progress, "Planning current Synctify library in dry-run sandbox...")
    connection.execute("SAVEPOINT synctify_library_update_preview")
    try:
        _reconcile_local_library(connection, library_dir, progress)
        resolution_sources, resolutions = _run_resolution_priority(
            connection,
            search_provider,
            resolve_sources,
            search_results=search_results,
            resolution_limit=resolution_limit,
            preview=True,
            progress=progress,
        )
        _notify(progress, "Planning downloads...")
        acquisitions = _group_pending_acquisitions(
            connection,
            acquisition_provider_factory,
        )
        _notify(progress, "Checking playlist readiness...")
        readiness = playlist_readiness(connection)
    finally:
        connection.execute("ROLLBACK TO SAVEPOINT synctify_library_update_preview")
        connection.execute("RELEASE SAVEPOINT synctify_library_update_preview")

    _notify(progress, "Dry-run preview complete.")
    return UpdateWorkflowReport(
        spotify=_unchanged_spotify_plan(),
        resolution_sources=resolution_sources,
        resolutions=resolutions,
        acquisitions=acquisitions,
        playlist_readiness=readiness,
        playlists=None,
        dry_run=True,
    )


def run_library_update_workflow(
    connection: sqlite3.Connection,
    search_provider: CatalogSearchProvider,
    resolve_sources: str | Sequence[str],
    acquisition_provider_factory: AcquisitionProviderFactory,
    library_dir: Path,
    playlists_dir: Path,
    *,
    search_results: int = 10,
    resolution_limit: int | None = None,
    allow_partial: bool = False,
    progress: ProgressReporter | None = _stderr_progress,
) -> UpdateWorkflowReport:
    """Resolve/acquire/rebuild only the playlists already confirmed in Synctify."""
    _notify(progress, "Using confirmed Synctify desired state. Spotify is not fetched by this command.")
    _reconcile_local_library(connection, library_dir, progress)
    connection.commit()

    resolution_sources, resolutions = _run_resolution_priority(
        connection,
        search_provider,
        resolve_sources,
        search_results=search_results,
        resolution_limit=resolution_limit,
        preview=False,
        progress=progress,
    )
    connection.commit()

    _notify(progress, "Planning downloads...")
    planned_acquisitions = _group_pending_acquisitions(
        connection,
        acquisition_provider_factory,
    )
    acquisitions = _run_acquisitions(
        connection,
        planned_acquisitions,
        acquisition_provider_factory,
        library_dir,
        progress=progress,
    )

    _notify(progress, "Building playlists...")
    playlists = build_playlists(
        connection,
        playlists_dir,
        allow_partial=allow_partial,
    )
    _notify(progress, "Library update workflow complete.")
    return UpdateWorkflowReport(
        spotify=_unchanged_spotify_plan(),
        resolution_sources=resolution_sources,
        resolutions=resolutions,
        acquisitions=acquisitions,
        playlist_readiness=None,
        playlists=playlists,
        dry_run=False,
        allow_partial=allow_partial,
    )
