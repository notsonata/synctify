from __future__ import annotations

import typer

from . import __version__
from .self_update import UpdateError, find_update, github_client, install_release, resolve_github_token


def self_update_command(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check the latest stable release without installing it.",
    ),
) -> None:
    """Check for or install the latest stable Synctify release."""
    token = resolve_github_token()
    try:
        with github_client(token) as client:
            release = find_update(__version__, client)
            if release is None:
                typer.echo(f"Synctify {__version__} is up to date.")
                return
            if check:
                typer.echo(
                    f"Synctify {release.version} is available (current: {__version__})."
                )
                return
            typer.echo(f"Updating Synctify {__version__} -> {release.version}...")
            install_release(release, client)
    except UpdateError as exc:
        typer.echo(f"Self-update failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Synctify updated to {release.version}.")
    typer.echo("The stable `synctify` command will use the new version on its next launch.")
