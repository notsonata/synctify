from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .entrypoint import app
from .setup import (
    SetupError,
    SetupOptions,
    format_setup_report,
    load_spotify_config,
    run_setup,
)
from .spotify.auth import (
    DEFAULT_REDIRECT_URI,
    KeyringTokenStore,
    SpotifyAuthError,
    interactive_login,
)
from .user_config import UserConfigError, load_user_config


def _prompt_text(label: str, current: str, supplied: str | None) -> str:
    if supplied is not None:
        return supplied
    return str(typer.prompt(label, default=current))


def _prompt_int(label: str, current: int, supplied: int | None) -> int:
    if supplied is not None:
        return supplied
    return int(typer.prompt(label, default=current, type=int))


@app.command("setup")
def setup_command(
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Do not prompt. Apply only explicitly supplied values and existing defaults.",
    ),
    source_priority: str | None = typer.Option(
        None,
        "--sources",
        help="Comma-separated automatic source priority.",
    ),
    qobuz_dl: str | None = typer.Option(
        None,
        "--qobuz-dl",
        help="qobuz-dl executable/path to save.",
    ),
    streamrip: str | None = typer.Option(
        None,
        "--streamrip",
        help="Streamrip `rip` executable/path to save.",
    ),
    rclone: str | None = typer.Option(
        None,
        "--rclone",
        help="rclone executable/path to save.",
    ),
    qobuz_quality: int | None = typer.Option(
        None,
        "--qobuz-quality",
        help="Saved qobuz-dl quality: 5, 6, 7, or 27.",
    ),
    streamrip_qobuz_quality: int | None = typer.Option(
        None,
        "--streamrip-qobuz-quality",
        help="Saved Streamrip Qobuz quality: 0-4.",
    ),
    streamrip_tidal_quality: int | None = typer.Option(
        None,
        "--streamrip-tidal-quality",
        help="Saved Streamrip Tidal quality: 0-4.",
    ),
    streamrip_deezer_quality: int | None = typer.Option(
        None,
        "--streamrip-deezer-quality",
        help="Saved Streamrip Deezer quality: 0-4.",
    ),
    streamrip_soundcloud_quality: int | None = typer.Option(
        None,
        "--streamrip-soundcloud-quality",
        help="Saved Streamrip SoundCloud quality: 0-4.",
    ),
    spotify_client_id: str | None = typer.Option(
        None,
        "--spotify-client-id",
        help="Spotify developer application Client ID to save.",
    ),
    spotify_redirect_uri: str | None = typer.Option(
        None,
        "--spotify-redirect-uri",
        help=f"Spotify OAuth redirect URI. Default: {DEFAULT_REDIRECT_URI}",
    ),
    spotify_login: bool | None = typer.Option(
        None,
        "--spotify-login/--no-spotify-login",
        help="Open browser OAuth after saving Spotify configuration.",
    ),
    mirror_name: str | None = typer.Option(
        None,
        "--mirror-name",
        help="Optional filesystem mirror target name.",
    ),
    mirror_destination: Path | None = typer.Option(
        None,
        "--mirror-destination",
        help="Optional filesystem mirror destination.",
    ),
    backup_name: str | None = typer.Option(
        None,
        "--backup-name",
        help="Optional rclone backup target name.",
    ),
    backup_destination: str | None = typer.Option(
        None,
        "--backup-destination",
        help="Optional rclone backup destination, such as pcloud:Synctify.",
    ),
) -> None:
    """Initialize Synctify and guide first-run configuration."""
    settings = Settings.default()
    try:
        current = load_user_config(settings.home)
        existing_spotify = load_spotify_config(settings)
    except (UserConfigError, SpotifyAuthError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if not non_interactive:
        typer.echo("Synctify first-run setup")
        typer.echo(f"Home: {settings.home}")
        typer.echo("")

        source_priority = _prompt_text(
            "Source priority",
            ",".join(current.source_priority),
            source_priority,
        )
        qobuz_dl = _prompt_text("qobuz-dl executable", current.qobuz_dl, qobuz_dl)
        streamrip = _prompt_text("Streamrip executable", current.streamrip, streamrip)
        rclone = _prompt_text("rclone executable", current.rclone, rclone)
        qobuz_quality = _prompt_int(
            "qobuz-dl quality",
            current.qobuz_quality,
            qobuz_quality,
        )
        streamrip_qobuz_quality = _prompt_int(
            "Streamrip Qobuz quality",
            current.streamrip_qobuz_quality,
            streamrip_qobuz_quality,
        )
        streamrip_tidal_quality = _prompt_int(
            "Streamrip Tidal quality",
            current.streamrip_tidal_quality,
            streamrip_tidal_quality,
        )
        streamrip_deezer_quality = _prompt_int(
            "Streamrip Deezer quality",
            current.streamrip_deezer_quality,
            streamrip_deezer_quality,
        )
        streamrip_soundcloud_quality = _prompt_int(
            "Streamrip SoundCloud quality",
            current.streamrip_soundcloud_quality,
            streamrip_soundcloud_quality,
        )

        if spotify_client_id is None:
            current_client = existing_spotify.client_id if existing_spotify else ""
            spotify_client_id = str(
                typer.prompt(
                    "Spotify Client ID (leave blank to skip)",
                    default=current_client,
                    show_default=bool(current_client),
                )
            ).strip() or None
        if spotify_client_id is not None and spotify_redirect_uri is None:
            current_redirect = (
                existing_spotify.redirect_uri if existing_spotify else DEFAULT_REDIRECT_URI
            )
            spotify_redirect_uri = str(
                typer.prompt("Spotify redirect URI", default=current_redirect)
            )
        if spotify_login is None:
            spotify_login = bool(
                spotify_client_id
                and typer.confirm("Log in to Spotify now?", default=True)
            )

        if mirror_name is None and mirror_destination is None:
            if typer.confirm("Configure a filesystem mirror target now?", default=False):
                mirror_name = str(typer.prompt("Mirror target name", default="phone"))
                mirror_destination = Path(
                    str(typer.prompt("Mirror destination, e.g. /Volumes/Phone/Music"))
                )
        if backup_name is None and backup_destination is None:
            if typer.confirm("Configure an rclone backup target now?", default=False):
                backup_name = str(typer.prompt("Backup target name", default="cloud"))
                backup_destination = str(
                    typer.prompt("Backup destination, e.g. pcloud:Synctify")
                )

    options = SetupOptions(
        source_priority=source_priority,
        qobuz_dl=qobuz_dl,
        streamrip=streamrip,
        rclone=rclone,
        qobuz_quality=qobuz_quality,
        streamrip_qobuz_quality=streamrip_qobuz_quality,
        streamrip_tidal_quality=streamrip_tidal_quality,
        streamrip_deezer_quality=streamrip_deezer_quality,
        streamrip_soundcloud_quality=streamrip_soundcloud_quality,
        spotify_client_id=spotify_client_id,
        spotify_redirect_uri=spotify_redirect_uri,
        mirror_name=mirror_name,
        mirror_destination=mirror_destination,
        backup_name=backup_name,
        backup_destination=backup_destination,
    )
    try:
        report = run_setup(settings, options)
    except (SetupError, UserConfigError, SpotifyAuthError, OSError) as exc:
        typer.echo(f"Setup failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_setup_report(report))

    should_login = bool(spotify_login) if spotify_login is not None else False
    if should_login:
        try:
            spotify_config = load_spotify_config(settings)
            if spotify_config is None:
                raise SpotifyAuthError(
                    "Spotify login was requested but no Client ID is configured."
                )
            interactive_login(spotify_config, KeyringTokenStore())
        except SpotifyAuthError as exc:
            typer.echo(f"Spotify login failed: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo("Spotify login complete; token saved to the system keychain.")
    elif report.spotify_configured:
        typer.echo("Spotify OAuth is configured. Run `synctify spotify login` when ready.")
