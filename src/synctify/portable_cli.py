from __future__ import annotations

from pathlib import Path

import typer

from .config import Settings
from .portable import (
    PortableStateError,
    apply_import,
    build_portable_bundle,
    bundle_to_dict,
    format_import_plan,
    plan_import,
    read_portable_bundle,
    write_portable_bundle,
)
from .setup_cli import app


@app.command("export")
def export_command(
    destination: Path,
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing export file.",
    ),
) -> None:
    """Export portable Synctify setup metadata without Spotify tokens or library state."""
    settings = Settings.default()
    try:
        bundle = write_portable_bundle(settings, destination, overwrite=force)
    except PortableStateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    data = bundle_to_dict(bundle)
    typer.echo(f"Portable Synctify state exported: {destination.expanduser()}")
    typer.echo(f"  Config overrides: {len(bundle.config)}")
    typer.echo(f"  Spotify config: {'included' if bundle.spotify is not None else 'not configured'}")
    typer.echo(f"  Targets: {len(bundle.targets)}")
    typer.echo(f"  Format version: {data['format_version']}")
    typer.echo("  Spotify keychain tokens and local library state were not exported.")


@app.command("import")
def import_command(
    source: Path,
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the validated import. Without this flag, import is preview-only.",
    ),
) -> None:
    """Preview or apply portable Synctify setup metadata."""
    settings = Settings.default()
    try:
        bundle = read_portable_bundle(source)
        report = apply_import(settings, bundle) if apply else plan_import(settings, bundle)
    except PortableStateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(format_import_plan(report))
