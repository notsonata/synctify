from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Callable


class SyncMode(StrEnum):
    MIRROR = "mirror"
    BACKUP = "backup"


class RcloneUnavailableError(RuntimeError):
    pass


class SyncTargetNotFoundError(KeyError):
    pass


class UnsafeSyncTargetError(ValueError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class SyncTarget:
    name: str
    destination: str
    mode: SyncMode
    kind: str = "filesystem"
    id: int | None = None


@dataclass(slots=True, frozen=True)
class SyncCommandResult:
    label: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True, frozen=True)
class SyncReport:
    target: SyncTarget
    results: tuple[SyncCommandResult, ...]
    dry_run: bool

    @property
    def ok(self) -> bool:
        return len(self.results) == 2 and all(result.returncode == 0 for result in self.results)


def rclone_command(
    source: Path,
    target: SyncTarget,
    *,
    dry_run: bool = False,
    executable: str = "rclone",
) -> list[str]:
    operation = "sync" if target.mode is SyncMode.MIRROR else "copy"
    command = [
        executable,
        operation,
        str(source),
        target.destination,
        "--create-empty-src-dirs",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def destination_deletes_enabled(mode: SyncMode) -> bool:
    """Return whether source deletions should propagate to the destination."""
    return mode is SyncMode.MIRROR


def add_filesystem_target(
    connection: sqlite3.Connection,
    name: str,
    destination: Path,
) -> SyncTarget:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("target name cannot be empty")
    try:
        destination_text = str(destination.expanduser().resolve(strict=False))
    except OSError as exc:
        raise ValueError(f"could not resolve filesystem target destination: {exc}") from exc
    try:
        cursor = connection.execute(
            """
            INSERT INTO sync_targets(name, kind, destination, mode)
            VALUES (?, 'filesystem', ?, 'mirror')
            """,
            (cleaned_name, destination_text),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"sync target already exists: {cleaned_name}") from exc
    return SyncTarget(
        cleaned_name,
        destination_text,
        SyncMode.MIRROR,
        kind="filesystem",
        id=int(cursor.lastrowid),
    )


def list_sync_targets(connection: sqlite3.Connection) -> tuple[SyncTarget, ...]:
    rows = connection.execute(
        "SELECT id, name, kind, destination, mode FROM sync_targets ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return tuple(
        SyncTarget(
            name=row["name"],
            destination=row["destination"],
            mode=SyncMode(row["mode"]),
            kind=row["kind"],
            id=row["id"],
        )
        for row in rows
    )


def get_sync_target(connection: sqlite3.Connection, name: str) -> SyncTarget:
    row = connection.execute(
        "SELECT id, name, kind, destination, mode FROM sync_targets WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise SyncTargetNotFoundError(name)
    return SyncTarget(
        name=row["name"],
        destination=row["destination"],
        mode=SyncMode(row["mode"]),
        kind=row["kind"],
        id=row["id"],
    )


def remove_sync_target(connection: sqlite3.Connection, name: str) -> bool:
    cursor = connection.execute("DELETE FROM sync_targets WHERE name = ?", (name,))
    return cursor.rowcount > 0


def validate_filesystem_target(target: SyncTarget, app_home: Path) -> Path:
    if target.kind != "filesystem":
        raise ValueError(f"target {target.name!r} is not a filesystem target")
    if target.mode is not SyncMode.MIRROR:
        raise ValueError(f"target {target.name!r} is not configured for mirror mode")

    destination = Path(target.destination).expanduser()
    if not destination.is_absolute():
        raise UnsafeSyncTargetError(
            f"target destination is relative and unsafe for mirror mode: {destination}; "
            "remove and recreate the target"
        )
    if not destination.exists():
        raise UnsafeSyncTargetError(
            f"target destination is not mounted or does not exist: {destination}"
        )
    if not destination.is_dir():
        raise UnsafeSyncTargetError(f"target destination is not a directory: {destination}")

    resolved_destination = destination.resolve()
    resolved_home = app_home.expanduser().resolve()
    if (
        resolved_destination == resolved_home
        or resolved_destination.is_relative_to(resolved_home)
        or resolved_home.is_relative_to(resolved_destination)
    ):
        raise UnsafeSyncTargetError(
            "refusing to mirror into or around Synctify's application-data directory"
        )
    return resolved_destination


def filesystem_mirror_commands(
    library_dir: Path,
    playlists_dir: Path,
    target: SyncTarget,
    *,
    dry_run: bool = False,
    executable: str = "rclone",
) -> tuple[tuple[str, list[str]], ...]:
    destination = Path(target.destination).expanduser()
    library_target = SyncTarget(
        target.name,
        str(destination / "library"),
        SyncMode.MIRROR,
        kind="filesystem",
        id=target.id,
    )
    playlists_target = SyncTarget(
        target.name,
        str(destination / "playlists"),
        SyncMode.MIRROR,
        kind="filesystem",
        id=target.id,
    )
    return (
        (
            "library",
            rclone_command(
                library_dir,
                library_target,
                dry_run=dry_run,
                executable=executable,
            ),
        ),
        (
            "playlists",
            rclone_command(
                playlists_dir,
                playlists_target,
                dry_run=dry_run,
                executable=executable,
            ),
        ),
    )


def run_filesystem_mirror(
    connection: sqlite3.Connection,
    target: SyncTarget,
    *,
    app_home: Path,
    library_dir: Path,
    playlists_dir: Path,
    dry_run: bool = False,
    executable: str = "rclone",
    runner: Runner = subprocess.run,
) -> SyncReport:
    resolved_destination = validate_filesystem_target(target, app_home)
    resolved_target = SyncTarget(
        target.name,
        str(resolved_destination),
        target.mode,
        kind=target.kind,
        id=target.id,
    )

    if shutil.which(executable) is None:
        raise RcloneUnavailableError(
            f"{executable!r} was not found on PATH. Install rclone before syncing."
        )

    run_id: int | None = None
    if not dry_run and target.id is not None:
        cursor = connection.execute(
            "INSERT INTO sync_runs(target_id, started_at) VALUES (?, ?)",
            (target.id, datetime.now(timezone.utc).isoformat()),
        )
        run_id = int(cursor.lastrowid)

    results: list[SyncCommandResult] = []
    failed = 0
    for label, command in filesystem_mirror_commands(
        library_dir,
        playlists_dir,
        resolved_target,
        dry_run=dry_run,
        executable=executable,
    ):
        result = runner(command, text=True, capture_output=True, check=False)
        command_result = SyncCommandResult(
            label=label,
            command=tuple(command),
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
        results.append(command_result)
        if result.returncode != 0:
            failed = 1
            break

    if run_id is not None:
        connection.execute(
            """
            UPDATE sync_runs
            SET completed_at = ?, failed = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), failed, run_id),
        )

    return SyncReport(resolved_target, tuple(results), dry_run)
