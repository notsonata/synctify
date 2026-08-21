from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from synctify.config import Settings
from synctify.setup import SetupError, SetupOptions, run_setup
from synctify.setup_cli import app
from synctify.spotify.auth import SpotifyOAuthConfig
from synctify.user_config import load_user_config


def settings_for(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def fake_which(executable: str) -> str | None:
    values = {
        "/tools/qobuz-dl": "/tools/qobuz-dl",
        "/tools/rip": "/tools/rip",
        "/tools/rclone": "/tools/rclone",
    }
    return values.get(executable)


def test_run_setup_initializes_state_and_persists_defaults(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    report = run_setup(
        settings,
        SetupOptions(
            source_priority="tidal,qobuz",
            qobuz_dl="/tools/qobuz-dl",
            streamrip="/tools/rip",
            rclone="/tools/rclone",
            qobuz_quality=7,
            streamrip_tidal_quality=4,
            spotify_client_id="client-123",
        ),
        which=fake_which,
    )

    assert settings.library_dir.is_dir()
    assert settings.playlists_dir.is_dir()
    assert settings.database_path.is_file()
    assert report.config.source_priority == ("tidal", "qobuz")
    assert report.config.qobuz_quality == 7
    assert report.config.streamrip_tidal_quality == 4
    assert report.spotify_configured is True
    assert report.missing_tools == 0
    assert SpotifyOAuthConfig.load(settings.spotify_config_path).client_id == "client-123"

    with sqlite3.connect(settings.database_path) as connection:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "5"


def test_setup_targets_are_idempotent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    mirror = tmp_path / "phone"
    mirror.mkdir()
    options = SetupOptions(
        mirror_name="phone",
        mirror_destination=mirror,
        backup_name="cloud",
        backup_destination="pcloud:Synctify",
    )

    first = run_setup(settings, options, which=lambda _: None)
    second = run_setup(settings, options, which=lambda _: None)

    assert [item.created for item in first.targets] == [True, True]
    assert [item.created for item in second.targets] == [False, False]
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sync_targets").fetchone()[0] == 2


def test_setup_refuses_conflicting_target_replacement(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    first = tmp_path / "phone-a"
    second = tmp_path / "phone-b"
    first.mkdir()
    second.mkdir()

    run_setup(
        settings,
        SetupOptions(mirror_name="phone", mirror_destination=first),
        which=lambda _: None,
    )

    with pytest.raises(SetupError, match="already exists with different settings"):
        run_setup(
            settings,
            SetupOptions(mirror_name="phone", mirror_destination=second),
            which=lambda _: None,
        )


def test_setup_requires_complete_target_pairs(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)

    with pytest.raises(SetupError, match="both name and destination"):
        run_setup(
            settings,
            SetupOptions(mirror_name="phone"),
            which=lambda _: None,
        )


def test_setup_cli_non_interactive_is_scriptable(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "scripted"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--sources",
            "qobuz,tidal",
            "--qobuz-dl",
            "/custom/qobuz-dl",
            "--streamrip",
            "/custom/rip",
            "--qobuz-quality",
            "27",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Synctify setup" in result.stdout
    assert "Setup complete" in result.stdout
    config = load_user_config(home)
    assert config.source_priority == ("qobuz", "tidal")
    assert config.qobuz_dl == "/custom/qobuz-dl"
    assert config.streamrip == "/custom/rip"
    assert (home / "synctify.sqlite3").exists()


def test_setup_cli_can_handoff_to_spotify_login(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "spotify"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    seen: list[str] = []

    def fake_login(config, store) -> None:
        seen.append(config.client_id)

    monkeypatch.setattr("synctify.setup_cli.interactive_login", fake_login)

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--spotify-client-id",
            "client-xyz",
            "--spotify-login",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["client-xyz"]
    assert "Spotify login complete" in result.stdout
