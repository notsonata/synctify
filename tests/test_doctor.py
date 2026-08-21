from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import time

from typer.testing import CliRunner

from synctify.backup import add_rclone_backup_target
from synctify.config import Settings
from synctify.db import initialize
from synctify.doctor import CheckStatus, run_doctor
from synctify.entrypoint import app
from synctify.spotify.auth import OAuthToken, SpotifyOAuthConfig
from synctify.sync import add_filesystem_target


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "synctify"
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def ready_settings(tmp_path: Path) -> Settings:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    initialize(settings.database_path)
    SpotifyOAuthConfig("client-123456", "http://127.0.0.1:8765/callback").save(
        settings.spotify_config_path
    )
    return settings


def fake_token(_: str) -> OAuthToken:
    return OAuthToken("access", "refresh", time.time() + 3600)


def fake_which(executable: str) -> str | None:
    mapping = {
        "qobuz-dl": "/usr/local/bin/qobuz-dl",
        "rip": "/opt/homebrew/bin/rip",
        "rclone": "/opt/homebrew/bin/rclone",
    }
    return mapping.get(executable)


def rclone_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
    assert command[-1] == "listremotes"
    return subprocess.CompletedProcess(command, 0, stdout="pcloud:\nother:\n", stderr="")


def test_doctor_reports_healthy_complete_setup(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    mirror = tmp_path / "phone"
    mirror.mkdir()
    with sqlite3.connect(settings.database_path) as connection:
        add_filesystem_target(connection, "phone", mirror)
        add_rclone_backup_target(connection, "cloud", "pcloud:Synctify")

    report = run_doctor(
        settings,
        which=fake_which,
        runner=rclone_runner,
        token_loader=fake_token,
    )

    assert report.failures == 0
    assert report.warnings == 0
    assert report.ok is True
    assert any(
        check.section == "targets"
        and check.name == "phone"
        and check.status is CheckStatus.PASS
        for check in report.checks
    )
    assert any(
        check.section == "targets"
        and check.name == "cloud"
        and check.status is CheckStatus.PASS
        for check in report.checks
    )


def test_uninitialized_optional_setup_is_warning_only_and_read_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    report = run_doctor(
        settings,
        which=lambda _: None,
        token_loader=lambda _: None,
    )

    assert report.failures == 0
    assert report.warnings >= 1
    assert report.ok is True
    assert settings.home.exists() is False
    assert settings.database_path.exists() is False


def test_corrupt_database_is_a_failure(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    settings.database_path.write_bytes(b"not sqlite")

    report = run_doctor(
        settings,
        which=lambda _: None,
        token_loader=lambda _: None,
    )

    assert report.failures >= 1
    assert any(
        check.section == "database" and check.status is CheckStatus.FAIL
        for check in report.checks
    )


def test_older_schema_with_pending_table_is_warning_not_corruption(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TABLE generated_playlists")
        connection.execute(
            "UPDATE metadata SET value = '4' WHERE key = 'schema_version'"
        )

    report = run_doctor(
        settings,
        which=fake_which,
        runner=rclone_runner,
        token_loader=fake_token,
    )

    schema = next(
        check
        for check in report.checks
        if check.section == "database" and check.name == "schema"
    )
    tables = next(
        check
        for check in report.checks
        if check.section == "database" and check.name == "tables"
    )
    assert schema.status is CheckStatus.WARN
    assert tables.status is CheckStatus.WARN
    assert "synctify init" in schema.message
    assert report.failures == 0


def test_current_schema_missing_generated_table_is_failure(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TABLE generated_playlists")

    report = run_doctor(
        settings,
        which=fake_which,
        runner=rclone_runner,
        token_loader=fake_token,
    )

    tables = next(
        check
        for check in report.checks
        if check.section == "database" and check.name == "tables"
    )
    assert tables.status is CheckStatus.FAIL
    assert "generated_playlists" in tables.message


def test_unsafe_mirror_overlap_is_a_failure(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        add_filesystem_target(connection, "unsafe", settings.home)

    report = run_doctor(
        settings,
        which=fake_which,
        runner=rclone_runner,
        token_loader=fake_token,
    )

    target = next(check for check in report.checks if check.name == "unsafe")
    assert target.status is CheckStatus.FAIL
    assert "overlaps" in target.message


def test_missing_configured_rclone_remote_is_warning(tmp_path: Path) -> None:
    settings = ready_settings(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        add_rclone_backup_target(connection, "cloud", "missing:Synctify")

    report = run_doctor(
        settings,
        which=fake_which,
        runner=rclone_runner,
        token_loader=fake_token,
    )

    target = next(check for check in report.checks if check.name == "cloud")
    assert target.status is CheckStatus.WARN
    assert "not configured" in target.message
    assert report.failures == 0


def test_doctor_cli_does_not_initialize_missing_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "never-created"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Synctify doctor" in result.stdout
    assert "warning(s)" in result.stdout
    assert home.exists() is False
