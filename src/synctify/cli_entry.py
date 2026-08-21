from __future__ import annotations

import os

import typer

from .auto_resolution import auto_resolve_tracks, format_auto_resolution_report
from .cli import _acquisition_provider, app, resolve_app
from .config import Settings
from .db import connect, initialize
from .gc import clean_unreferenced_tracks, format_cleanup_report
from .providers.streamrip import StreamripUnavailableError
from .providers.streamrip_search import StreamripCatalogSearch, StreamripSearchConfig
from .spotify.auth import SpotifyAuth, SpotifyAuthError, SpotifyOAuthConfig
from .spotify.client import SpotifyAPIError, SpotifyClient
from .spotify.ingest import SpotifySnapshot, fetch_spotify_snapshot
from .workflow import (
    DEFAULT_SOURCE_PRIORITY,
    format_update_workflow_report,
    normalize_source_priority,
    preview_update_workflow,
    run_update_workflow,
)


def _fetch_update_snapshot(settings: Settings) -> SpotifySnapshot:
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    auth = SpotifyAuth(config)
    with SpotifyClient(auth) as client:
        return fetch_spotify_snapshot(client)


def _update_acquisition_provider(
    source: str,
    qobuz_dl: str | None,
    streamrip: str | None,
):
    try:
        return _acquisition_provider(
            source,
            None,
            None,
            qobuz_dl,
            streamrip,
        )
    except typer.BadParameter as exc:
        raise ValueError(str(exc)) from exc


def _update_source_priority(
    search_provider: StreamripCatalogSearch,
    *,
    source: str | None,
    sources: str,
) -> tuple[str, ...]:
    raw_sources = (source,) if source is not None else tuple(sources.split(","))
    try:
        priority = normalize_source_priority(raw_sources)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    unsupported = [item for item in priority if not search_provider.supports(item)]
    if unsupported:
        supported = ", ".join(sorted(search_provider.supported_sources))
        raise typer.BadParameter(
            f"unsupported source(s): {', '.join(unsupported)}; supported: {supported}"
        )
    return priority


@resolve_app.command("auto")
def resolve_auto_command(
    source: str = typer.Option(
        "qobuz",
        "--source",
        help="Source catalog to search: qobuz, tidal, deezer, or soundcloud.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Search and score candidates without saving automatic resolutions.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Maximum number of unresolved Spotify tracks to inspect.",
    ),
    search_results: int = typer.Option(
        10,
        "--search-results",
        min=1,
        help="Maximum Streamrip results requested for each catalog query.",
    ),
    streamrip: str | None = typer.Option(
        None,
        "--streamrip",
        help="Path to the Streamrip `rip` executable.",
    ),
) -> None:
    """Automatically search a source catalog and persist only safe deterministic matches."""
    normalized_source = source.strip().lower()
    executable = streamrip or os.getenv("SYNCTIFY_STREAMRIP", "rip")
    search_provider = StreamripCatalogSearch(
        StreamripSearchConfig(
            executable=executable,
            results_per_query=search_results,
        )
    )
    if not search_provider.supports(normalized_source):
        supported = ", ".join(sorted(search_provider.supported_sources))
        raise typer.BadParameter(f"source must be one of: {supported}")
    try:
        search_provider.require_available()
    except StreamripUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        report = auto_resolve_tracks(
            connection,
            search_provider,
            normalized_source,
            limit=limit,
            search_results=search_results,
            dry_run=dry_run,
        )

    typer.echo(format_auto_resolution_report(report))
    if report.failed:
        raise typer.Exit(code=2)


@app.command("update")
def coordinated_update_command(
    source: str | None = typer.Option(
        None,
        "--source",
        help="Use only one automatic-resolution catalog, overriding --sources.",
    ),
    sources: str = typer.Option(
        ",".join(DEFAULT_SOURCE_PRIORITY),
        "--sources",
        help="Comma-separated automatic-resolution fallback order.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview post-pull resolution/download work without changing local state or files.",
    ),
    search_results: int = typer.Option(
        10,
        "--search-results",
        min=1,
        help="Maximum catalog candidates requested for each automatic search query.",
    ),
    resolution_limit: int | None = typer.Option(
        None,
        "--resolution-limit",
        min=1,
        help="Maximum number of distinct unresolved desired tracks to search in this run.",
    ),
    allow_partial: bool = typer.Option(
        False,
        "--allow-partial",
        help="Allow generated playlists to omit tracks that remain unavailable locally.",
    ),
    qobuz_dl: str | None = typer.Option(
        None,
        "--qobuz-dl",
        help="Path to the external qobuz-dl executable used for Qobuz acquisitions.",
    ),
    streamrip: str | None = typer.Option(
        None,
        "--streamrip",
        help="Path to the Streamrip `rip` executable used for catalog search and non-Qobuz acquisition.",
    ),
) -> None:
    """Refresh Spotify, resolve with source fallbacks, acquire audio, and rebuild playlists."""
    streamrip_executable = streamrip or os.getenv("SYNCTIFY_STREAMRIP", "rip")
    search_provider = StreamripCatalogSearch(
        StreamripSearchConfig(
            executable=streamrip_executable,
            results_per_query=search_results,
        )
    )
    priority = _update_source_priority(
        search_provider,
        source=source,
        sources=sources,
    )

    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)

    try:
        snapshot = _fetch_update_snapshot(settings)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    provider_factory = lambda task_source: _update_acquisition_provider(
        task_source,
        qobuz_dl,
        streamrip,
    )

    try:
        with connect(settings.database_path) as connection:
            if dry_run:
                report = preview_update_workflow(
                    connection,
                    snapshot,
                    search_provider,
                    priority,
                    provider_factory,
                    search_results=search_results,
                    resolution_limit=resolution_limit,
                )
            else:
                report = run_update_workflow(
                    connection,
                    snapshot,
                    search_provider,
                    priority,
                    provider_factory,
                    settings.library_dir,
                    settings.playlists_dir,
                    search_results=search_results,
                    resolution_limit=resolution_limit,
                    allow_partial=allow_partial,
                )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Update failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_update_workflow_report(report))
    if not dry_run and report.playlists is not None and report.playlists.incomplete:
        typer.echo(
            "Some playlists remain incomplete. Resolve/acquire the missing tracks or use --allow-partial.",
            err=True,
        )
    if report.operational_failures:
        raise typer.Exit(code=2)


@app.command("clean")
def clean_command(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Delete safe unreferenced local files. Without this flag, clean is preview-only.",
    ),
) -> None:
    """Preview or remove local tracks no longer referenced by any current playlist."""
    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)

    with connect(settings.database_path) as connection:
        report = clean_unreferenced_tracks(
            connection,
            settings.library_dir,
            apply=apply,
        )

    typer.echo(format_cleanup_report(report))
    if report.failures:
        for failure in report.failures:
            typer.echo(
                f"Failed to delete {failure.spotify_id} at {failure.path}: {failure.message}",
                err=True,
            )
        raise typer.Exit(code=2)
