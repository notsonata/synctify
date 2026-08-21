from __future__ import annotations

import os

import typer

from .cli_entry import app
from .config import Settings
from .doctor import format_doctor_report, run_doctor


@app.command("doctor")
def doctor_command(
    qobuz_dl: str | None = typer.Option(
        None,
        "--qobuz-dl",
        help="qobuz-dl executable/path to inspect. Defaults to SYNCTIFY_QOBUZ_DL or qobuz-dl.",
    ),
    streamrip: str | None = typer.Option(
        None,
        "--streamrip",
        help="Streamrip executable/path to inspect. Defaults to SYNCTIFY_STREAMRIP or rip.",
    ),
    rclone: str = typer.Option(
        "rclone",
        "--rclone",
        help="rclone executable/path to inspect.",
    ),
) -> None:
    """Diagnose Synctify configuration, local state, tools, and targets without changing them."""
    settings = Settings.default()
    report = run_doctor(
        settings,
        qobuz_dl=qobuz_dl or os.getenv("SYNCTIFY_QOBUZ_DL", "qobuz-dl"),
        streamrip=streamrip or os.getenv("SYNCTIFY_STREAMRIP", "rip"),
        rclone=rclone,
    )
    typer.echo(format_doctor_report(report))
    if report.failures:
        raise typer.Exit(code=2)
