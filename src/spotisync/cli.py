from __future__ import annotations

import sqlite3

import typer

from .config import Settings
from .db import connect, initialize

app = typer.Typer(
    name="spotisync",
    help="Local-first Spotify playlist and lossless music library synchronizer.",
    no_args_is_help=True,
)


@app.command()
def init() -> None:
    """Create the local SpotiSync directories and SQLite database."""
    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)
    typer.echo(f"Initialized SpotiSync at {settings.home}")


@app.command()
def status() -> None:
    """Show the current local library state."""
    settings = Settings.default()
    if not settings.database_path.exists():
        typer.echo("SpotiSync is not initialized. Run: spotisync init")
        raise typer.Exit(code=1)

    try:
        with connect(settings.database_path) as connection:
            tracks = connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            local_tracks = connection.execute(
                "SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL"
            ).fetchone()[0]
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM tracks WHERE status = 'unresolved'"
            ).fetchone()[0]
            playlists = connection.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo("SpotiSync")
    typer.echo(f"  Tracks:      {tracks}")
    typer.echo(f"  Local FLACs: {local_tracks}")
    typer.echo(f"  Unresolved:  {unresolved}")
    typer.echo(f"  Playlists:   {playlists}")
    typer.echo(f"  Library:     {settings.library_dir}")


if __name__ == "__main__":
    app()
