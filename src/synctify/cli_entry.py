from __future__ import annotations

import typer

from .cli import app
from .config import Settings
from .db import connect, initialize
from .gc import clean_unreferenced_tracks, format_cleanup_report


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
