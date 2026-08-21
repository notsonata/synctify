from __future__ import annotations

import os
import typer

from .auto_resolution import auto_resolve_tracks, format_auto_resolution_report
from .cli import app, resolve_app
from .config import Settings
from .db import connect, initialize
from .gc import clean_unreferenced_tracks, format_cleanup_report
from .providers.streamrip import StreamripUnavailableError
from .providers.streamrip_search import StreamripCatalogSearch, StreamripSearchConfig


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
