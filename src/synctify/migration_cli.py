from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .migration import (
    MigrationError,
    MigrationOptions,
    format_migration_report,
    preview_migration,
    run_migration,
)
from .relink_cli import app


@app.command("migrate")
def migrate_command(
    portable_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Portable Synctify JSON exported from the previous Mac.",
    ),
    library: Path | None = typer.Option(
        None,
        "--library",
        exists=False,
        file_okay=False,
        dir_okay=True,
        readable=False,
        resolve_path=False,
        help=(
            "Optional existing FLAC tree to relink/copy after Spotify desired state is restored. "
            "A disconnected source is accepted only when --resume skips an already-completed relink stage."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Run the migration. Without this flag, validate and show the planned stages only.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume a matching migration checkpoint and skip stages already completed safely.",
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Replace an existing migration checkpoint and run all stages again.",
    ),
    spotify_login: bool = typer.Option(
        False,
        "--spotify-login",
        help="Launch Spotify browser OAuth after importing the portable public Spotify configuration.",
    ),
    allow_partial: bool = typer.Option(
        False,
        "--allow-partial",
        help="Allow generated playlists to omit tracks that remain unavailable after relink.",
    ),
    relink_limit: int | None = typer.Option(
        None,
        "--relink-limit",
        min=1,
        help="Inspect at most this many missing desired tracks during the optional relink stage.",
    ),
) -> None:
    """Restore or resume Synctify setup, desired state, local library links, and diagnostics."""
    if resume and restart:
        raise typer.BadParameter("--resume and --restart cannot be used together")

    settings = Settings.default()
    options = MigrationOptions(
        portable_file=portable_file,
        library_source=library,
        spotify_login=spotify_login,
        allow_partial=allow_partial,
        relink_limit=relink_limit,
        resume=resume,
        restart=restart,
    )

    try:
        report = run_migration(settings, options) if apply else preview_migration(settings, options)
    except MigrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_migration_report(report))
    if report.applied and report.playlists is not None and report.playlists.incomplete:
        typer.echo(
            "Some playlists remain incomplete. Run `synctify audit` and acquire or relink the missing tracks.",
            err=True,
        )
    if report.applied and report.checkpoint is not None and not report.checkpoint.complete:
        typer.echo(
            "Migration checkpoint is incomplete. Fix the reported stage and rerun with --resume.",
            err=True,
        )
    if report.operational_failures:
        raise typer.Exit(code=2)
