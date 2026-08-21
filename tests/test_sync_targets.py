from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from synctify.db import connect, initialize
from synctify.sync import (
    SyncMode,
    SyncTarget,
    UnsafeSyncTargetError,
    add_filesystem_target,
    filesystem_mirror_commands,
    get_sync_target,
    list_sync_targets,
    remove_sync_target,
    run_filesystem_mirror,
    validate_filesystem_target,
)


def test_filesystem_target_crud(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    with connect(database) as connection:
        created = add_filesystem_target(connection, "phone", Path("/Volumes/Phone/Music"))
        loaded = get_sync_target(connection, "phone")
        listed = list_sync_targets(connection)
        removed = remove_sync_target(connection, "phone")
        after = list_sync_targets(connection)

    assert created.name == "phone"
    assert created.kind == "filesystem"
    assert created.mode is SyncMode.MIRROR
    assert loaded == created
    assert listed == (created,)
    assert removed is True
    assert after == ()


def test_filesystem_mirror_preserves_library_playlist_sibling_layout(tmp_path: Path) -> None:
    library = tmp_path / "home" / "library"
    playlists = tmp_path / "home" / "playlists"
    destination = tmp_path / "device"
    target = SyncTarget("phone", str(destination), SyncMode.MIRROR)

    commands = filesystem_mirror_commands(
        library,
        playlists,
        target,
        dry_run=True,
        executable="/usr/local/bin/rclone",
    )

    assert commands[0][0] == "library"
    assert commands[0][1] == [
        "/usr/local/bin/rclone",
        "sync",
        str(library),
        str(destination / "library"),
        "--create-empty-src-dirs",
        "--dry-run",
    ]
    assert commands[1][0] == "playlists"
    assert commands[1][1][3] == str(destination / "playlists")
    assert commands[1][1][-1] == "--dry-run"


def test_unmounted_destination_is_rejected(tmp_path: Path) -> None:
    target = SyncTarget(
        "phone",
        str(tmp_path / "not-mounted"),
        SyncMode.MIRROR,
    )

    with pytest.raises(UnsafeSyncTargetError, match="not mounted or does not exist"):
        validate_filesystem_target(target, tmp_path / "home")


def test_destination_overlapping_synctify_home_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "synctify-home"
    destination = home / "device"
    destination.mkdir(parents=True)
    target = SyncTarget("bad", str(destination), SyncMode.MIRROR)

    with pytest.raises(UnsafeSyncTargetError, match="refusing to mirror"):
        validate_filesystem_target(target, home)


def test_run_filesystem_mirror_runs_both_roots_and_records_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "synctify.sqlite3"
    home = tmp_path / "home"
    library = home / "library"
    playlists = home / "playlists"
    destination = tmp_path / "device"
    library.mkdir(parents=True)
    playlists.mkdir(parents=True)
    destination.mkdir()
    initialize(database)
    monkeypatch.setattr("synctify.sync.shutil.which", lambda _: "/usr/local/bin/rclone")
    commands: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    with connect(database) as connection:
        target = add_filesystem_target(connection, "phone", destination)
        report = run_filesystem_mirror(
            connection,
            target,
            app_home=home,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
        )
        sync_run = connection.execute(
            "SELECT completed_at, failed FROM sync_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()

    assert report.ok is True
    assert len(commands) == 2
    assert commands[0][1] == "sync"
    assert commands[0][3] == str(destination.resolve() / "library")
    assert commands[1][3] == str(destination.resolve() / "playlists")
    assert sync_run["completed_at"] is not None
    assert sync_run["failed"] == 0


def test_run_filesystem_mirror_stops_after_failure_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "synctify.sqlite3"
    home = tmp_path / "home"
    library = home / "library"
    playlists = home / "playlists"
    destination = tmp_path / "device"
    library.mkdir(parents=True)
    playlists.mkdir(parents=True)
    destination.mkdir()
    initialize(database)
    monkeypatch.setattr("synctify.sync.shutil.which", lambda _: "/usr/local/bin/rclone")
    calls = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 7, stdout="", stderr="failed")

    with connect(database) as connection:
        target = add_filesystem_target(connection, "phone", destination)
        report = run_filesystem_mirror(
            connection,
            target,
            app_home=home,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
        )
        sync_run = connection.execute(
            "SELECT failed FROM sync_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()

    assert report.ok is False
    assert calls == 1
    assert len(report.results) == 1
    assert report.results[0].label == "library"
    assert sync_run["failed"] == 1


def test_dry_run_does_not_record_sync_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "synctify.sqlite3"
    home = tmp_path / "home"
    library = home / "library"
    playlists = home / "playlists"
    destination = tmp_path / "device"
    library.mkdir(parents=True)
    playlists.mkdir(parents=True)
    destination.mkdir()
    initialize(database)
    monkeypatch.setattr("synctify.sync.shutil.which", lambda _: "/usr/local/bin/rclone")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert "--dry-run" in command
        return subprocess.CompletedProcess(command, 0, stdout="dry", stderr="")

    with connect(database) as connection:
        target = add_filesystem_target(connection, "phone", destination)
        report = run_filesystem_mirror(
            connection,
            target,
            app_home=home,
            library_dir=library,
            playlists_dir=playlists,
            dry_run=True,
            runner=runner,
        )
        count = connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]

    assert report.ok is True
    assert report.dry_run is True
    assert count == 0
