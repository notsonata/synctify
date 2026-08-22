from __future__ import annotations

import typer

# Import command callables only. These modules still expose their historical Typer
# apps for compatibility, but the installed CLI below does not reuse those mutable
# app objects or depend on their decorator/import order.
from .cli import (
    _settings_with_database,
    init,
    playlists_build,
    resolve_clear,
    resolve_status,
    spotify_login,
    spotify_logout,
    spotify_pull,
    targets_add,
    targets_add_backup,
    targets_list,
    targets_remove,
)
from .cli_entry import audit_command, clean_command
from .db import connect
from .entrypoint import (
    config_set,
    config_show,
    config_unset,
    configured_acquire,
    configured_backup,
    configured_doctor,
    configured_qobuz_doctor,
    configured_qobuz_download_url,
    configured_resolve_auto,
    configured_status,
    configured_streamrip_doctor,
    configured_sync,
    configured_update,
)
from .migration_cli import migrate_command
from .portable_cli import export_command, import_command
from .relink_cli import relink_command
from .resolution import set_manual_override
from .setup_cli import setup_command


app = typer.Typer(
    name="synctify",
    help="Local-first Spotify playlist and lossless music library synchronizer.",
    no_args_is_help=True,
)

spotify_app = typer.Typer(help="Authenticate with Spotify and import desired library state.")
resolve_app = typer.Typer(help="Inspect and manage source-service track resolutions.")
qobuz_app = typer.Typer(help="Inspect and run the external qobuz-dl downloader.")
streamrip_app = typer.Typer(help="Inspect the external Streamrip downloader.")
playlists_app = typer.Typer(help="Build M3U8 playlists from local Synctify state.")
targets_app = typer.Typer(help="Manage mirror and backup targets.")
config_app = typer.Typer(help="Inspect and manage persistent Synctify defaults.")

app.add_typer(spotify_app, name="spotify")
app.add_typer(resolve_app, name="resolve")
app.add_typer(qobuz_app, name="qobuz")
app.add_typer(streamrip_app, name="streamrip")
app.add_typer(playlists_app, name="playlists")
app.add_typer(targets_app, name="targets")
app.add_typer(config_app, name="config")


@resolve_app.command("set")
def resolve_set_command(
    spotify_id: str,
    provider: str = typer.Argument(
        ...,
        help="Source provider: qobuz, tidal, or deezer.",
    ),
    provider_track_id: str = typer.Argument(..., help="Provider track identifier."),
) -> None:
    """Persist a manual source-service mapping for one Spotify track."""
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


# Core top-level commands.
app.command("init")(init)
app.command("status")(configured_status)
app.command("acquire")(configured_acquire)
app.command("update")(configured_update)
app.command("sync")(configured_sync)
app.command("backup")(configured_backup)
app.command("doctor")(configured_doctor)
app.command("audit")(audit_command)
app.command("clean")(clean_command)
app.command("setup")(setup_command)
app.command("export")(export_command)
app.command("import")(import_command)
app.command("relink")(relink_command)
app.command("migrate")(migrate_command)

# Spotify commands.
spotify_app.command("login")(spotify_login)
spotify_app.command("logout")(spotify_logout)
spotify_app.command("pull")(spotify_pull)

# Resolution commands. Use the configured automatic resolver; status/clear are the
# single core implementations and set has a validation-aware wrapper above.
resolve_app.command("status")(resolve_status)
resolve_app.command("clear")(resolve_clear)
resolve_app.command("auto")(configured_resolve_auto)

# Downloader commands use the configured wrappers so persistent defaults retain
# their existing precedence.
qobuz_app.command("doctor")(configured_qobuz_doctor)
qobuz_app.command("download-url")(configured_qobuz_download_url)
streamrip_app.command("doctor")(configured_streamrip_doctor)

playlists_app.command("build")(playlists_build)

targets_app.command("add")(targets_add)
targets_app.command("add-backup")(targets_add_backup)
targets_app.command("list")(targets_list)
targets_app.command("remove")(targets_remove)

config_app.command("show")(config_show)
config_app.command("set")(config_set)
config_app.command("unset")(config_unset)


if __name__ == "__main__":
    app()
