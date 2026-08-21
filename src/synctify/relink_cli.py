from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .db import connect
from .portable_cli import app
from .relink import RelinkError, format_relink_report, relink_library


@app.command("relink")
def relink_command(
    source: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Existing FLAC library/tree to scan for desired Spotify tracks.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Copy/adopt safe matches and update SQLite. Without this flag, relink is preview-only.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Inspect at most this many desired tracks that do not already have a usable canonical file.",
    ),
) -> None:
    """Relink a copied FLAC library to desired tracks without downloading audio."""
    settings = Settings.default()
    if not settings.database_path.exists():
        typer.echo(
            "Synctify has no local desired-state database. Run `synctify setup`, authenticate Spotify, "
            "then run `synctify spotify pull` before relinking.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        with connect(settings.database_path) as connection:
            report = relink_library(
                connection,
                source,
                settings.library_dir,
                apply=apply,
                limit=limit,
            )
    except RelinkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_relink_report(report))
    if report.failures:
        raise typer.Exit(code=2)
