from __future__ import annotations

import sqlite3

import typer

from .config import Settings
from .db import connect, initialize
from .spotify.auth import DEFAULT_REDIRECT_URI, KeyringTokenStore, SpotifyAuth, SpotifyAuthError, SpotifyOAuthConfig, interactive_login, resolve_config
from .spotify.client import SpotifyAPIError, SpotifyClient
from .spotify.ingest import fetch_spotify_snapshot
from .spotify.state import ChangePlan, apply_snapshot, format_plan, plan_snapshot

app = typer.Typer(name="synctify", help="Local-first Spotify playlist and lossless music library synchronizer.", no_args_is_help=True)
spotify_app = typer.Typer(help="Authenticate with Spotify and import desired library state.")
app.add_typer(spotify_app, name="spotify")


def _settings_with_database() -> Settings:
    settings = Settings.default()
    settings.ensure_directories()
    initialize(settings.database_path)
    return settings


def _fetch_and_plan(*, apply: bool) -> ChangePlan:
    settings = _settings_with_database()
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    auth = SpotifyAuth(config)
    with SpotifyClient(auth) as client:
        snapshot = fetch_spotify_snapshot(client)
    with connect(settings.database_path) as connection:
        plan = plan_snapshot(connection, snapshot)
        if apply:
            apply_snapshot(connection, snapshot)
    return plan


@app.command()
def init() -> None:
    """Create the local Synctify directories and SQLite database."""
    settings = _settings_with_database()
    typer.echo(f"Initialized Synctify at {settings.home}")


@app.command()
def status() -> None:
    """Show the current local library state."""
    settings = Settings.default()
    if not settings.database_path.exists():
        typer.echo("Synctify is not initialized. Run: synctify init")
        raise typer.Exit(code=1)
    try:
        with connect(settings.database_path) as connection:
            tracks = connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            local_tracks = connection.execute("SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL").fetchone()[0]
            unresolved = connection.execute("SELECT COUNT(*) FROM tracks WHERE status = 'unresolved'").fetchone()[0]
            playlists = connection.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
            last_pull = connection.execute("SELECT value FROM metadata WHERE key = 'spotify_last_pull_at'").fetchone()
    except sqlite3.DatabaseError as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("Synctify")
    typer.echo(f"  Tracks:      {tracks}")
    typer.echo(f"  Local FLACs: {local_tracks}")
    typer.echo(f"  Unresolved:  {unresolved}")
    typer.echo(f"  Playlists:   {playlists}")
    typer.echo(f"  Spotify:     {last_pull[0] if last_pull else 'never pulled'}")
    typer.echo(f"  Library:     {settings.library_dir}")


@spotify_app.command("login")
def spotify_login(client_id: str | None = typer.Option(None, "--client-id", help="Spotify developer app Client ID."), redirect_uri: str | None = typer.Option(None, "--redirect-uri", help=f"OAuth callback URI. Default: {DEFAULT_REDIRECT_URI}")) -> None:
    """Authenticate Synctify using Spotify Authorization Code with PKCE."""
    settings = _settings_with_database()
    try:
        config = resolve_config(settings.spotify_config_path, client_id=client_id, redirect_uri=redirect_uri)
        typer.echo(f"Opening Spotify login. Redirect URI: {config.redirect_uri}")
        interactive_login(config, KeyringTokenStore())
    except SpotifyAuthError as exc:
        typer.echo(f"Spotify login failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("Spotify login saved to macOS Keychain.")


@spotify_app.command("logout")
def spotify_logout() -> None:
    """Remove the stored Spotify OAuth token from the system keychain."""
    settings = Settings.default()
    try:
        config = SpotifyOAuthConfig.load(settings.spotify_config_path)
        KeyringTokenStore().clear(config.client_id)
    except SpotifyAuthError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Spotify token removed from the system keychain.")


@spotify_app.command("pull")
def spotify_pull() -> None:
    """Import Liked Songs and accessible owned/collaborative playlists into local state."""
    try:
        plan = _fetch_and_plan(apply=True)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_plan(plan))
    typer.echo("Spotify desired state updated.")


@app.command()
def update(dry_run: bool = typer.Option(False, "--dry-run", help="Fetch Spotify and show changes without modifying local state.")) -> None:
    """Refresh Spotify desired state. Acquisition and device sync are added in later stages."""
    try:
        plan = _fetch_and_plan(apply=not dry_run)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_plan(plan))
    if dry_run:
        typer.echo("Dry run only. Local state was not changed.")
    else:
        typer.echo("Spotify desired state updated. Audio acquisition is not implemented yet.")


if __name__ == "__main__":
    app()
