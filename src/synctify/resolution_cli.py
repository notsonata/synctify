from __future__ import annotations

import typer

from .cli import _settings_with_database, resolve_app
from .db import connect
from .entrypoint import app
from .resolution import set_manual_override


@resolve_app.command("set")
def resolve_set_command(spotify_id: str, provider: str, provider_track_id: str) -> None:
    """Persist a supported manual source-service mapping for one Spotify track."""
    settings = _settings_with_database()
    normalized_provider = provider.strip().lower()
    try:
        with connect(settings.database_path) as connection:
            set_manual_override(
                connection,
                spotify_id,
                normalized_provider,
                provider_track_id,
            )
    except KeyError as exc:
        typer.echo(f"Unknown Spotify track: {spotify_id}", err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Mapped {spotify_id} -> {normalized_provider}:{provider_track_id}")
