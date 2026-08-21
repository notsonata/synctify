from __future__ import annotations

import os
from pathlib import Path

import typer

from . import cli_entry as cli_entry_module
from .cli import (
    _qobuz_config,
    acquire as base_acquire,
    app,
    backup_command as base_backup,
    qobuz_app,
    qobuz_doctor as base_qobuz_doctor,
    qobuz_download_url as base_qobuz_download_url,
    streamrip_app,
    streamrip_doctor as base_streamrip_doctor,
    sync_command as base_sync,
)
from .cli_entry import (
    resolve_app,
    resolve_auto_command as base_resolve_auto,
)
from .config import Settings
from .db import connect, initialize
from .doctor import format_doctor_report, run_doctor
from .providers.qobuz import QobuzDLProvider
from .providers.streamrip import StreamripConfig, StreamripProvider
from .providers.streamrip_search import StreamripCatalogSearch, StreamripSearchConfig
from .spotify.auth import SpotifyAuthError
from .spotify.client import SpotifyAPIError
from .user_config import (
    UserConfig,
    UserConfigError,
    format_user_config,
    load_user_config,
    resolve_qobuz_dl,
    resolve_qobuz_quality,
    resolve_rclone,
    resolve_source_priority,
    resolve_streamrip,
    resolve_streamrip_quality,
    set_user_config,
    unset_user_config,
)
from .workflow import (
    format_update_workflow_report,
    preview_update_workflow,
    run_update_workflow,
)

config_app = typer.Typer(help="Inspect and manage persistent Synctify defaults.")
app.add_typer(config_app, name="config")


def _settings_and_user_config() -> tuple[Settings, UserConfig]:
    settings = Settings.default()
    try:
        config = load_user_config(settings.home)
    except UserConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    return settings, config


@config_app.command("show")
def config_show() -> None:
    """Show effective persistent defaults and the config file location."""
    settings, config = _settings_and_user_config()
    typer.echo(format_user_config(config, settings.home))


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set one persistent default, using hyphenated or underscored key names."""
    settings = Settings.default()
    try:
        config = set_user_config(settings.home, key, value)
    except UserConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_user_config(config, settings.home))


@config_app.command("unset")
def config_unset(key: str) -> None:
    """Remove one saved override and fall back to environment/built-in defaults."""
    settings = Settings.default()
    try:
        config, existed = unset_user_config(settings.home, key)
    except UserConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    if not existed:
        typer.echo(f"No saved override for {key}.")
    typer.echo(format_user_config(config, settings.home))


@resolve_app.command("auto")
def configured_resolve_auto(
    source: str = typer.Option("qobuz", "--source"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    search_results: int = typer.Option(10, "--search-results", min=1),
    streamrip: str | None = typer.Option(None, "--streamrip"),
) -> None:
    """Automatically resolve tracks using the configured Streamrip executable default."""
    _, config = _settings_and_user_config()
    base_resolve_auto(
        source=source,
        dry_run=dry_run,
        limit=limit,
        search_results=search_results,
        streamrip=resolve_streamrip(config, streamrip),
    )


@qobuz_app.command("doctor")
def configured_qobuz_doctor(
    executable: str | None = typer.Option(None, "--executable"),
) -> None:
    """Check qobuz-dl using the configured executable default."""
    _, config = _settings_and_user_config()
    base_qobuz_doctor(executable=resolve_qobuz_dl(config, executable))


@qobuz_app.command("download-url")
def configured_qobuz_download_url(
    url: str,
    destination: Path = typer.Option(..., "--destination", "-d", file_okay=False),
    quality: int | None = typer.Option(None, "--quality", "-q"),
    executable: str | None = typer.Option(None, "--executable"),
) -> None:
    """Download a Qobuz URL using configured executable and quality defaults."""
    _, config = _settings_and_user_config()
    base_qobuz_download_url(
        url=url,
        destination=destination,
        quality=resolve_qobuz_quality(config, quality),
        executable=resolve_qobuz_dl(config, executable),
    )


@streamrip_app.command("doctor")
def configured_streamrip_doctor(
    executable: str | None = typer.Option(None, "--executable"),
) -> None:
    """Check Streamrip using the configured executable default."""
    _, config = _settings_and_user_config()
    base_streamrip_doctor(executable=resolve_streamrip(config, executable))


@app.command("acquire")
def configured_acquire(
    source: str = typer.Option("qobuz", "--source"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    limit: int | None = typer.Option(None, "--limit", min=1),
    downloader: str | None = typer.Option(None, "--downloader"),
    quality: int | None = typer.Option(None, "--quality", "-q"),
    qobuz_dl: str | None = typer.Option(None, "--qobuz-dl"),
    streamrip: str | None = typer.Option(None, "--streamrip"),
) -> None:
    """Acquire resolved tracks using persistent downloader defaults when flags are omitted."""
    _, config = _settings_and_user_config()
    normalized_source = source.strip().lower()
    selected_downloader = (
        downloader.strip().lower()
        if downloader is not None
        else ("qobuz-dl" if normalized_source == "qobuz" else "streamrip")
    )
    selected_quality = quality
    if selected_quality is None:
        selected_quality = (
            resolve_qobuz_quality(config)
            if selected_downloader == "qobuz-dl"
            else resolve_streamrip_quality(config, normalized_source)
        )
    base_acquire(
        source=normalized_source,
        dry_run=dry_run,
        limit=limit,
        downloader=downloader,
        quality=selected_quality,
        qobuz_dl=resolve_qobuz_dl(config, qobuz_dl),
        streamrip=resolve_streamrip(config, streamrip),
    )


def _configured_update_provider(
    config: UserConfig,
    source: str,
    qobuz_dl: str,
    streamrip: str,
):
    normalized = source.strip().lower()
    if normalized == "qobuz":
        return QobuzDLProvider(
            _qobuz_config(
                qobuz_dl,
                resolve_qobuz_quality(config),
                managed=True,
            )
        )
    return StreamripProvider(
        StreamripConfig(
            executable=streamrip,
            quality=resolve_streamrip_quality(config, normalized),
        )
    )


@app.command("update")
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
    """Run the coordinated update using saved source, executable, and quality defaults."""
    settings, config = _settings_and_user_config()
    resolved_streamrip = resolve_streamrip(config, streamrip)
    resolved_qobuz_dl = resolve_qobuz_dl(config, qobuz_dl)
    configured_sources = resolve_source_priority(config, sources)

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

    settings.ensure_directories()
    initialize(settings.database_path)
    try:
        snapshot = cli_entry_module._fetch_update_snapshot(settings)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    provider_factory = lambda task_source: _configured_update_provider(
        config,
        task_source,
        resolved_qobuz_dl,
        resolved_streamrip,
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


@app.command("sync")
def configured_sync(
    target_name: str,
    dry_run: bool = typer.Option(False, "--dry-run"),
    rclone: str | None = typer.Option(None, "--rclone"),
) -> None:
    """Mirror to a filesystem target using the configured rclone executable default."""
    _, config = _settings_and_user_config()
    base_sync(
        target_name=target_name,
        dry_run=dry_run,
        rclone=resolve_rclone(config, rclone),
    )


@app.command("backup")
def configured_backup(
    target_name: str,
    dry_run: bool = typer.Option(False, "--dry-run"),
    rclone: str | None = typer.Option(None, "--rclone"),
) -> None:
    """Back up to an rclone target using the configured executable default."""
    _, config = _settings_and_user_config()
    base_backup(
        target_name=target_name,
        dry_run=dry_run,
        rclone=resolve_rclone(config, rclone),
    )


@app.command("doctor")
def configured_doctor(
    qobuz_dl: str | None = typer.Option(None, "--qobuz-dl"),
    streamrip: str | None = typer.Option(None, "--streamrip"),
    rclone: str | None = typer.Option(None, "--rclone"),
) -> None:
    """Diagnose Synctify using persistent executable defaults when flags are omitted."""
    settings, config = _settings_and_user_config()
    report = run_doctor(
        settings,
        qobuz_dl=resolve_qobuz_dl(config, qobuz_dl),
        streamrip=resolve_streamrip(config, streamrip),
        rclone=resolve_rclone(config, rclone),
    )
    typer.echo(format_doctor_report(report))
    if report.failures:
        raise typer.Exit(code=2)
