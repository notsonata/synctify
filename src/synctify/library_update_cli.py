from __future__ import annotations

import typer

from . import cli_entry as cli_entry_module
from .db import connect, initialize
from .entrypoint import (
    _configured_update_provider,
    _resolve_config_value,
    _settings_and_user_config,
)
from .library_update import preview_library_update_workflow, run_library_update_workflow
from .providers.streamrip_search import StreamripCatalogSearch, StreamripSearchConfig
from .update_preview import preview_database_connection
from .user_config import (
    resolve_qobuz_dl,
    resolve_source_priority,
    resolve_streamrip,
)
from .workflow import format_update_workflow_report


def configured_update(
    source: str | None = typer.Option(None, "--source"),
    sources: str | None = typer.Option(None, "--sources"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    search_results: int = typer.Option(10, "--search-results", min=1),
    resolution_limit: int | None = typer.Option(None, "--resolution-limit", min=1),
    allow_partial: bool = typer.Option(False, "--allow-partial"),
    qobuz_dl: str | None = typer.Option(None, "--qobuz-dl"),
    streamrip: str | None = typer.Option(None, "--streamrip"),
) -> None:
    """Resolve, acquire, and rebuild the already-confirmed Synctify library."""
    settings, config = _settings_and_user_config()
    resolved_streamrip = resolve_streamrip(config, streamrip)
    resolved_qobuz_dl = resolve_qobuz_dl(config, qobuz_dl)
    configured_sources = _resolve_config_value(
        lambda: resolve_source_priority(config, sources)
    )

    search_provider = StreamripCatalogSearch(
        StreamripSearchConfig(
            executable=resolved_streamrip,
            results_per_query=search_results,
        )
    )
    priority = cli_entry_module._update_source_priority(
        search_provider,
        source=source,
        sources=",".join(configured_sources),
    )

    if not dry_run:
        settings.ensure_directories()
        initialize(settings.database_path)

    provider_factory = lambda task_source: _configured_update_provider(
        config,
        task_source,
        resolved_qobuz_dl,
        resolved_streamrip,
    )

    try:
        if dry_run:
            with preview_database_connection(settings.database_path) as connection:
                report = preview_library_update_workflow(
                    connection,
                    search_provider,
                    priority,
                    provider_factory,
                    search_results=search_results,
                    resolution_limit=resolution_limit,
                )
        else:
            with connect(settings.database_path) as connection:
                report = run_library_update_workflow(
                    connection,
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
