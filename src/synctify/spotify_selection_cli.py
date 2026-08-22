from __future__ import annotations

import typer

from .config import Settings
from .db import connect, initialize
from .spotify.auth import SpotifyAuth, SpotifyAuthError, SpotifyOAuthConfig
from .spotify.client import SpotifyAPIError, SpotifyClient
from .spotify.selection import (
    SpotifySelectionCancelled,
    fetch_playlist_catalog,
    store_playlist_catalog,
    update_tracked_playlists,
)


def _spotify_client(settings: Settings) -> SpotifyClient:
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    return SpotifyClient(SpotifyAuth(config))


def fetch_playlists_command() -> None:
    """Fetch the Spotify playlist catalog without importing playlist tracks."""
    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)
    try:
        with _spotify_client(settings) as client:
            user_id, entries = fetch_playlist_catalog(
                client,
                progress=lambda message: typer.echo(f"[spotify] {message}", err=True),
            )
        with connect(settings.database_path) as connection:
            store_playlist_catalog(connection, user_id, entries)
    except (SpotifyAuthError, SpotifyAPIError, SpotifySelectionCancelled) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Fetched {len(entries)} Spotify playlist(s). No playlist tracks were imported.")


def update_tracked_command() -> None:
    """Fetch track changes only for Spotify playlists already tracked by Synctify."""
    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)
    try:
        with connect(settings.database_path) as connection:
            with _spotify_client(settings) as client:
                count = update_tracked_playlists(
                    connection,
                    client,
                    progress=lambda message: typer.echo(f"[spotify] {message}", err=True),
                )
    except (SpotifyAuthError, SpotifyAPIError, SpotifySelectionCancelled) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"Checked {count} tracked Spotify playlist(s). New tracks and removals remain pending until reviewed."
    )
