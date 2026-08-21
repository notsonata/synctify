from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from synctify.backup import (
    add_rclone_backup_target,
    backup_commands,
    pending_playlist_snapshots,
    run_rclone_backup,
)
from synctify.db import connect, initialize


def _setup(tmp_path: Path):
    database = tmp_path / "synctify.sqlite3"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    library.mkdir()
    playlists.mkdir()
    (library / "track.flac").write_bytes(b"flac")
    (playlists / "Driving.m3u8").write_text(
        "#EXTM3U\n../library/track.flac\n",
        encoding="utf-8",
    )
    initialize(database)
    return database, library, playlists


def test_add_rclone_backup_target_requires_remote_syntax(tmp_path: Path) -> None:
    database, _, _ = _setup(tmp_path)
    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:Synctify")
        row = connection.execute(
            "SELECT kind, mode, destination FROM sync_targets WHERE name = 'pcloud'"
        ).fetchone()

        assert target.kind == "rclone"
        assert target.mode.value == "backup"
        assert row["destination"] == "pcloud:Synctify"

        with pytest.raises(ValueError, match="rclone remote path"):
            add_rclone_backup_target(connection, "bad", "/tmp/not-a-remote")


def test_backup_commands_never_sync_the_library(tmp_path: Path) -> None:
    database, library, playlists = _setup(tmp_path)
    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:MusicBackup")
        snapshots = pending_playlist_snapshots(
            connection,
            target,
            playlists,
            timestamp="2026-08-21T150000Z",
        )
        commands = backup_commands(library, playlists, target, snapshots)

    assert commands[0][0] == "library"
    assert commands[0][1][1] == "copy"
    assert commands[0][1][3] == "pcloud:MusicBackup/library"
    assert commands[1][1][1] == "copyto"
    assert "playlists/snapshots/Driving/2026-08-21T150000Z.m3u8" in commands[1][1][3]
    assert commands[-1][0] == "playlists-current"
    assert commands[-1][1][1] == "sync"
    assert commands[-1][1][3] == "pcloud:MusicBackup/playlists/current"


def test_successful_backup_records_snapshot_and_skips_unchanged_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database, library, playlists = _setup(tmp_path)
    monkeypatch.setattr("synctify.backup.shutil.which", lambda _: "/opt/homebrew/bin/rclone")
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:MusicBackup")
        first = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
        )
        snapshots_after_first = connection.execute(
            "SELECT playlist_file, sha256, remote_path FROM playlist_backup_snapshots"
        ).fetchall()
        second = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc),
        )
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM playlist_backup_snapshots"
        ).fetchone()[0]
        run_count = connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]

    assert first.ok is True
    assert len(first.snapshots) == 1
    assert len(snapshots_after_first) == 1
    assert snapshots_after_first[0]["playlist_file"] == "Driving.m3u8"
    assert second.ok is True
    assert second.snapshots == ()
    assert snapshot_count == 1
    assert run_count == 2
    assert [command[1] for command in calls].count("copyto") == 1


def test_playlist_change_creates_a_new_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database, library, playlists = _setup(tmp_path)
    monkeypatch.setattr("synctify.backup.shutil.which", lambda _: "/usr/local/bin/rclone")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:MusicBackup")
        run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
        )
        (playlists / "Driving.m3u8").write_text(
            "#EXTM3U\n../library/track.flac\n../library/new.flac\n",
            encoding="utf-8",
        )
        report = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc),
        )
        rows = connection.execute(
            "SELECT remote_path FROM playlist_backup_snapshots ORDER BY id"
        ).fetchall()

    assert len(report.snapshots) == 1
    assert len(rows) == 2
    assert rows[0]["remote_path"].endswith("2026-08-21T150000Z.m3u8")
    assert rows[1]["remote_path"].endswith("2026-08-21T173000Z.m3u8")


def test_dry_run_does_not_record_snapshot_or_sync_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database, library, playlists = _setup(tmp_path)
    monkeypatch.setattr("synctify.backup.shutil.which", lambda _: "/usr/local/bin/rclone")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert "--dry-run" in command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:MusicBackup")
        report = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            dry_run=True,
            runner=runner,
            now=datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc),
        )
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM playlist_backup_snapshots"
        ).fetchone()[0]
        run_count = connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]

    assert report.ok is True
    assert snapshot_count == 0
    assert run_count == 0


def test_backup_stops_after_library_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database, library, playlists = _setup(tmp_path)
    monkeypatch.setattr("synctify.backup.shutil.which", lambda _: "/usr/local/bin/rclone")
    calls = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="network down")

    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "pcloud", "pcloud:MusicBackup")
        report = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
        )
        failed = connection.execute(
            "SELECT failed FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM playlist_backup_snapshots"
        ).fetchone()[0]

    assert report.ok is False
    assert calls == 1
    assert failed == 1
    assert snapshot_count == 0
