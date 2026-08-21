from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import typer

from .acquisition import acquire_tasks, format_acquisition_plan, pending_acquisitions
from .config import Settings
from .db import connect, initialize
from .playlists import build_playlists, format_build_report
from .providers.qobuz import (
    QOBUZ_DL_REPOSITORY,
    QobuzDLConfig,
    QobuzDLDownloadError,
    QobuzDLProvider,
    QobuzDLUnavailableError,
)
from .providers.streamrip import (
    STREAMRIP_REPOSITORY,
    StreamripConfig,
    StreamripDownloadError,
    StreamripProvider,
    StreamripUnavailableError,
)
from .resolution import clear_resolution, set_manual_override
from .spotify.auth import DEFAULT_REDIRECT_URI, KeyringTokenStore, SpotifyAuth, SpotifyAuthError, SpotifyOAuthConfig, interactive_login, resolve_config
from .spotify.client import SpotifyAPIError, SpotifyClient
from .spotify.ingest import fetch_spotify_snapshot
from .spotify.state import ChangePlan, apply_snapshot, format_plan, plan_snapshot

app = typer.Typer(name="synctify", help="Local-first Spotify playlist and lossless music library synchronizer.", no_args_is_help=True)
spotify_app = typer.Typer(help="Authenticate with Spotify and import desired library state.")
resolve_app = typer.Typer(help="Inspect and manage provider track resolutions.")
qobuz_app = typer.Typer(help="Inspect and run the external qobuz-dl downloader.")
streamrip_app = typer.Typer(help="Inspect the external streamrip downloader.")
playlists_app = typer.Typer(help="Build M3U8 playlists from local Synctify state.")
app.add_typer(spotify_app, name="spotify")
app.add_typer(resolve_app, name="resolve")
app.add_typer(qobuz_app, name="qobuz")
app.add_typer(streamrip_app, name="streamrip")
app.add_typer(playlists_app, name="playlists")


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


def _qobuz_executable(value: str | None) -> str:
    return value or os.getenv("SYNCTIFY_QOBUZ_DL", "qobuz-dl")


def _streamrip_executable(value: str | None) -> str:
    return value or os.getenv("SYNCTIFY_STREAMRIP", "rip")


def _qobuz_config(executable: str | None, quality: int, *, managed: bool = False) -> QobuzDLConfig:
    if quality not in {5, 6, 7, 27}:
        raise typer.BadParameter("qobuz-dl quality must be one of: 5, 6, 7, 27")
    extra_args = ("--no-db",) if managed else ()
    return QobuzDLConfig(
        executable=_qobuz_executable(executable),
        quality=quality,
        extra_args=extra_args,
    )


def _acquisition_provider(
    downloader: str,
    quality: int | None,
    qobuz_dl: str | None,
    streamrip: str | None,
):
    normalized = downloader.strip().lower()
    if normalized == "qobuz-dl":
        return QobuzDLProvider(_qobuz_config(qobuz_dl, 27 if quality is None else quality, managed=True))
    if normalized == "streamrip":
        selected_quality = 4 if quality is None else quality
        if selected_quality not in {0, 1, 2, 3, 4}:
            raise typer.BadParameter("streamrip quality must be between 0 and 4")
        return StreamripProvider(
            StreamripConfig(
                executable=_streamrip_executable(streamrip),
                quality=selected_quality,
            )
        )
    raise typer.BadParameter("downloader must be one of: qobuz-dl, streamrip")


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
            pending = len(pending_acquisitions(connection))
            last_pull = connection.execute("SELECT value FROM metadata WHERE key = 'spotify_last_pull_at'").fetchone()
    except sqlite3.DatabaseError as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo("Synctify")
    typer.echo(f"  Tracks:      {tracks}")
    typer.echo(f"  Local FLACs: {local_tracks}")
    typer.echo(f"  Unresolved:  {unresolved}")
    typer.echo(f"  Resolutions: {resolutions}")
    typer.echo(f"  Pending DL:  {pending}")
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
def qobuz_doctor(executable: str | None = typer.Option(None, "--executable")) -> None:
    """Check whether the separately installed qobuz-dl executable is available."""
    resolved = _qobuz_executable(executable)
    provider = QobuzDLProvider(QobuzDLConfig(executable=resolved))
    if not provider.is_available():
        typer.echo(f"qobuz-dl not found: {resolved}", err=True)
        typer.echo(f"Clone and set up: {QOBUZ_DL_REPOSITORY}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"qobuz-dl available: {resolved}")


@qobuz_app.command("download-url")
def qobuz_download_url(
    url: str,
    destination: Path = typer.Option(..., "--destination", "-d", file_okay=False),
    quality: int = typer.Option(27, "--quality", "-q"),
    executable: str | None = typer.Option(None, "--executable"),
) -> None:
    """Acquire a Qobuz URL through the external qobuz-dl executable."""
    provider = QobuzDLProvider(_qobuz_config(executable, quality))
    try:
        files = provider.acquire_url(url, destination)
    except (QobuzDLUnavailableError, QobuzDLDownloadError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"qobuz-dl completed. New FLAC files: {len(files)}")
    for path in files:
        typer.echo(str(path))


@streamrip_app.command("doctor")
def streamrip_doctor(executable: str | None = typer.Option(None, "--executable")) -> None:
    """Check whether the external streamrip executable is available."""
    resolved = _streamrip_executable(executable)
    provider = StreamripProvider(StreamripConfig(executable=resolved))
    if not provider.is_available():
        typer.echo(f"streamrip not found: {resolved}", err=True)
        typer.echo(f"Install or clone: {STREAMRIP_REPOSITORY}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"streamrip available: {resolved}")


@app.command()
def acquire(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show resolved tracks waiting for download."),
    limit: int | None = typer.Option(None, "--limit", min=1),
    downloader: str = typer.Option("qobuz-dl", "--downloader", help="External downloader: qobuz-dl or streamrip."),
    quality: int | None = typer.Option(None, "--quality", "-q", help="Downloader-specific quality. qobuz-dl defaults to 27; streamrip defaults to 4."),
    qobuz_dl: str | None = typer.Option(None, "--qobuz-dl", help="Path to the qobuz-dl executable."),
    streamrip: str | None = typer.Option(None, "--streamrip", help="Path to the streamrip `rip` executable."),
) -> None:
    """Download already-resolved Qobuz tracks into the canonical local library."""
    settings = _settings_with_database()
    with connect(settings.database_path) as connection:
        tasks = pending_acquisitions(connection, provider="qobuz", limit=limit)
        typer.echo(format_acquisition_plan(tasks))
        if dry_run or not tasks:
            return

        provider = _acquisition_provider(downloader, quality, qobuz_dl, streamrip)
        try:
            provider.require_available()
        except (QobuzDLUnavailableError, StreamripUnavailableError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc

        report = acquire_tasks(connection, provider, tasks, settings.library_dir)

    typer.echo(f"Downloader: {downloader}")
    typer.echo(f"Acquired:   {report.succeeded}")
    typer.echo(f"Failed:     {report.failed}")
    for failure in report.failures:
        typer.echo(f"  {failure.spotify_id}: {failure.message}", err=True)
    if report.failed:
        raise typer.Exit(code=2)


@playlists_app.command("build")
def playlists_build(
    allow_partial: bool = typer.Option(False, "--allow-partial", help="Write playlists with acquired tracks even when some tracks are missing."),
) -> None:
    """Build UTF-8 M3U8 playlists from persisted Spotify order and local FLAC paths."""
    settings = _settings_with_database()
    with connect(settings.database_path) as connection:
        report = build_playlists(connection, settings.playlists_dir, allow_partial=allow_partial)
    typer.echo(format_build_report(report))
    typer.echo(f"Playlist directory: {settings.playlists_dir}")
    if report.incomplete and not allow_partial:
        raise typer.Exit(code=2)


@app.command()
def update(dry_run: bool = typer.Option(False, "--dry-run", help="Fetch Spotify and show changes without modifying local state.")) -> None:
    """Refresh Spotify desired state. Acquisition remains an explicit separate step."""
    try:
        plan = _fetch_and_plan(apply=not dry_run)
    except (SpotifyAuthError, SpotifyAPIError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(format_plan(plan))
    if dry_run:
        typer.echo("Dry run only. Local state was not changed.")
    else:
        typer.echo("Spotify desired state updated. Run `synctify acquire --dry-run` to inspect pending downloads.")


if __name__ == "__main__":
    app()
