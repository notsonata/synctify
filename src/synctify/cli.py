from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from .config import Settings
from .db import connect, initialize
from .providers.qobuz import QobuzDLConfig, QobuzDLDownloadError, QobuzDLProvider, QobuzDLUnavailableError
from .resolution import clear_resolution, set_manual_override
from .spotify.auth import DEFAULT_REDIRECT_URI, KeyringTokenStore, SpotifyAuth, SpotifyAuthError, SpotifyOAuthConfig, interactive_login, resolve_config
from .spotify.client import SpotifyAPIError, SpotifyClient
from .spotify.ingest import fetch_spotify_snapshot
from .spotify.state import ChangePlan, apply_snapshot, format_plan, plan_snapshot

app = typer.Typer(name="synctify", help="Local-first Spotify playlist and lossless music library synchronizer.", no_args_is_help=True)
spotify_app = typer.Typer(help="Authenticate with Spotify and import desired library state.")
resolve_app = typer.Typer(help="Inspect and manage provider track resolutions.")
qobuz_app = typer.Typer(help="Inspect and run the qobuz-dl acquisition provider.")
app.add_typer(spotify_app, name="spotify")
app.add_typer(resolve_app, name="resolve")
app.add_typer(qobuz_app, name="qobuz")


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
            resolutions = connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0]
            last_pull = connection.execute("SELECT value FROM metadata WHERE key = 'spotify_last_pull_at'").fetchone()
    except sqlite3.DatabaseError as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("Synctify")
    typer.echo(f"  Tracks:      {tracks}")
    typer.echo(f"  Local FLACs: {local_tracks}")
    typer.echo(f"  Unresolved:  {unresolved}")
    typer.echo(f"  Resolutions: {resolutions}")
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


@resolve_app.command("status")
def resolve_status() -> None:
    """Show how many Spotify tracks have provider resolutions."""
    settings = _settings_with_database()
    with connect(settings.database_path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        resolved = connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0]
        manual = connection.execute("SELECT COUNT(*) FROM track_resolutions WHERE is_manual = 1").fetchone()[0]
    typer.echo("Track resolution")
    typer.echo(f"  Tracks:     {total}")
    typer.echo(f"  Resolved:   {resolved}")
    typer.echo(f"  Manual:     {manual}")
    typer.echo(f"  Unresolved: {max(total - resolved, 0)}")


@resolve_app.command("set")
def resolve_set(spotify_id: str, provider: str, provider_track_id: str) -> None:
    """Persist a manual provider mapping for one Spotify track."""
    settings = _settings_with_database()
    try:
        with connect(settings.database_path) as connection:
            set_manual_override(connection, spotify_id, provider, provider_track_id)
    except KeyError as exc:
        typer.echo(f"Unknown Spotify track: {spotify_id}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Mapped {spotify_id} -> {provider}:{provider_track_id}")


@resolve_app.command("clear")
def resolve_clear(spotify_id: str) -> None:
    """Remove an automatic or manual provider mapping."""
    settings = _settings_with_database()
    with connect(settings.database_path) as connection:
        removed = clear_resolution(connection, spotify_id)
    if not removed:
        typer.echo(f"No resolution stored for {spotify_id}")
        raise typer.Exit(code=1)
    typer.echo(f"Cleared resolution for {spotify_id}")


@qobuz_app.command("doctor")
def qobuz_doctor(executable: str = typer.Option("qobuz-dl", "--executable")) -> None:
    """Check whether the qobuz-dl executable is available."""
    provider = QobuzDLProvider(QobuzDLConfig(executable=executable))
    if not provider.is_available():
        typer.echo(f"qobuz-dl not found: {executable}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"qobuz-dl available: {executable}")


@qobuz_app.command("download-url")
def qobuz_download_url(
    url: str,
    destination: Path = typer.Option(..., "--destination", "-d", file_okay=False),
    quality: int = typer.Option(27, "--quality", "-q", min=5),
    executable: str = typer.Option("qobuz-dl", "--executable"),
) -> None:
    """Acquire a Qobuz URL through the installed qobuz-dl executable."""
    provider = QobuzDLProvider(QobuzDLConfig(executable=executable, quality=quality))
    try:
        files = provider.acquire_url(url, destination)
    except (QobuzDLUnavailableError, QobuzDLDownloadError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"qobuz-dl completed. New FLAC files: {len(files)}")
    for path in files:
        typer.echo(str(path))


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
