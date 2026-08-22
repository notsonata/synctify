from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Callable

from .sync import (
    RcloneUnavailableError,
    SyncCommandResult,
    SyncMode,
    SyncReport,
    SyncTarget,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class PlaylistSnapshot:
    playlist_file: str
    local_path: Path
    sha256: str
    remote_path: str


@dataclass(slots=True, frozen=True)
class BackupReport:
    target: SyncTarget
    results: tuple[SyncCommandResult, ...]
    snapshots: tuple[PlaylistSnapshot, ...]
    dry_run: bool

    @property
    def ok(self) -> bool:
        labels = {result.label for result in self.results}
        return (
            "library" in labels
            and "playlists-current" in labels
            and all(result.returncode == 0 for result in self.results)
        )


def _remote_join(root: str, *parts: str) -> str:
    cleaned = root.rstrip("/")
    suffix = "/".join(part.strip("/") for part in parts if part)
    return f"{cleaned}/{suffix}" if suffix else cleaned


def validate_rclone_backup_target(target: SyncTarget) -> None:
    if target.kind != "rclone":
        raise ValueError(f"target {target.name!r} is not an rclone backup target")
    if target.mode is not SyncMode.BACKUP:
        raise ValueError(f"target {target.name!r} is not configured for backup mode")
    remote, separator, _ = target.destination.partition(":")
    if not separator or not remote.strip():
        raise ValueError(
            "backup destination must be an rclone remote path such as pcloud:Synctify"
        )


def add_rclone_backup_target(
    connection: sqlite3.Connection,
    name: str,
    destination: str,
) -> SyncTarget:
    cleaned_name = name.strip()
    cleaned_destination = destination.strip().rstrip("/")
    if not cleaned_name:
        raise ValueError("target name cannot be empty")
    candidate = SyncTarget(
        cleaned_name,
        cleaned_destination,
        SyncMode.BACKUP,
        kind="rclone",
    )
    validate_rclone_backup_target(candidate)
    try:
        cursor = connection.execute(
            """
            INSERT INTO sync_targets(name, kind, destination, mode)
            VALUES (?, 'rclone', ?, 'backup')
            """,
            (cleaned_name, cleaned_destination),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"sync target already exists: {cleaned_name}") from exc
    return SyncTarget(
        cleaned_name,
        cleaned_destination,
        SyncMode.BACKUP,
        kind="rclone",
        id=int(cursor.lastrowid),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pending_playlist_snapshots(
    connection: sqlite3.Connection,
    target: SyncTarget,
    playlists_dir: Path,
    *,
    timestamp: str,
) -> tuple[PlaylistSnapshot, ...]:
    if target.id is None:
        raise ValueError("backup target must be persisted before snapshot planning")

    snapshots: list[PlaylistSnapshot] = []
    for path in sorted(playlists_dir.glob("*.m3u8"), key=lambda item: item.name.casefold()):
        sha256 = _file_sha256(path)
        latest = connection.execute(
            """
            SELECT sha256
            FROM playlist_backup_snapshots
            WHERE target_id = ? AND playlist_file = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target.id, path.name),
        ).fetchone()
        if latest is not None and latest["sha256"] == sha256:
            continue
        remote_path = _remote_join(
            target.destination,
            "playlists",
            "snapshots",
            path.stem,
            f"{timestamp}-{sha256[:12]}.m3u8",
        )
        snapshots.append(
            PlaylistSnapshot(
                playlist_file=path.name,
                local_path=path,
                sha256=sha256,
                remote_path=remote_path,
            )
        )
    return tuple(snapshots)


def _copy_command(
    source: Path,
    destination: str,
    *,
    executable: str,
    dry_run: bool,
) -> list[str]:
    command = [
        executable,
        "copy",
        str(source),
        destination,
        "--create-empty-src-dirs",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def _copyto_command(
    source: Path,
    destination: str,
    *,
    executable: str,
    dry_run: bool,
) -> list[str]:
    command = [executable, "copyto", str(source), destination]
    if dry_run:
        command.append("--dry-run")
    return command


def _current_playlists_command(
    playlists_dir: Path,
    destination: str,
    *,
    executable: str,
    dry_run: bool,
) -> list[str]:
    command = [
        executable,
        "sync",
        str(playlists_dir),
        _remote_join(destination, "playlists", "current"),
        "--create-empty-src-dirs",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def backup_commands(
    library_dir: Path,
    playlists_dir: Path,
    target: SyncTarget,
    snapshots: tuple[PlaylistSnapshot, ...],
    *,
    dry_run: bool = False,
    executable: str = "rclone",
) -> tuple[tuple[str, list[str]], ...]:
    commands: list[tuple[str, list[str]]] = [
        (
            "library",
            _copy_command(
                library_dir,
                _remote_join(target.destination, "library"),
                executable=executable,
                dry_run=dry_run,
            ),
        )
    ]
    for snapshot in snapshots:
        commands.append(
            (
                f"snapshot:{snapshot.playlist_file}",
                _copyto_command(
                    snapshot.local_path,
                    snapshot.remote_path,
                    executable=executable,
                    dry_run=dry_run,
                ),
            )
        )
    commands.append(
        (
            "playlists-current",
            _current_playlists_command(
                playlists_dir,
                target.destination,
                executable=executable,
                dry_run=dry_run,
            ),
        )
    )
    return tuple(commands)


def run_rclone_backup(
    connection: sqlite3.Connection,
    target: SyncTarget,
    *,
    library_dir: Path,
    playlists_dir: Path,
    dry_run: bool = False,
    executable: str = "rclone",
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> BackupReport:
    validate_rclone_backup_target(target)
    if shutil.which(executable) is None:
        raise RcloneUnavailableError(
            f"{executable!r} was not found on PATH. Install and configure rclone before backing up."
        )

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    timestamp = current_time.strftime("%Y-%m-%dT%H%M%SZ")
    snapshots = pending_playlist_snapshots(
        connection,
        target,
        playlists_dir,
        timestamp=timestamp,
    )

    run_id: int | None = None
    if not dry_run and target.id is not None:
        cursor = connection.execute(
            "INSERT INTO sync_runs(target_id, started_at) VALUES (?, ?)",
            (target.id, current_time.isoformat()),
        )
        run_id = int(cursor.lastrowid)

    results: list[SyncCommandResult] = []
    failed = 0
    snapshot_by_label = {
        f"snapshot:{snapshot.playlist_file}": snapshot for snapshot in snapshots
    }

    for label, command in backup_commands(
        library_dir,
        playlists_dir,
        target,
        snapshots,
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

        if not dry_run and label in snapshot_by_label and target.id is not None:
            snapshot = snapshot_by_label[label]
            connection.execute(
                """
                INSERT INTO playlist_backup_snapshots(
                    target_id, playlist_file, sha256, remote_path, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    target.id,
                    snapshot.playlist_file,
                    snapshot.sha256,
                    snapshot.remote_path,
                    current_time.isoformat(),
                ),
            )

    if run_id is not None:
        connection.execute(
            """
            UPDATE sync_runs
            SET completed_at = ?, failed = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), failed, run_id),
        )

    return BackupReport(target, tuple(results), snapshots, dry_run)
