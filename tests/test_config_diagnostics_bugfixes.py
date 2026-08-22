from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synctify.config import Settings
from synctify.db import initialize
from synctify.doctor import CheckStatus, run_doctor
from synctify.portable import (
    PortableStateError,
    build_portable_bundle,
    parse_portable_dict,
)
from synctify.setup import SetupError, SetupOptions, run_setup
from synctify.spotify.auth import DEFAULT_REDIRECT_URI, SpotifyOAuthConfig


def _settings(home: Path) -> Settings:
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def _portable_dict() -> dict[str, object]:
    return {
        "format": "synctify-portable",
        "format_version": 1,
        "synctify_version": "0.22.0",
        "config": {},
        "spotify": None,
        "targets": [],
    }


def test_portable_rejects_invalid_loopback_redirect_port() -> None:
    raw = _portable_dict()
    raw["spotify"] = {
        "client_id": "client",
        "redirect_uri": "http://127.0.0.1:not-a-port/callback",
    }

    with pytest.raises(PortableStateError):
        parse_portable_dict(raw)


def test_portable_rejects_invalid_rclone_target_before_apply() -> None:
    raw = _portable_dict()
    raw["targets"] = [
        {
            "name": "cloud",
            "kind": "rclone",
            "destination": "not-a-remote-path",
            "mode": "backup",
        }
    ]

    with pytest.raises(PortableStateError, match="rclone remote path"):
        parse_portable_dict(raw)


def test_portable_rejects_relative_filesystem_target() -> None:
    raw = _portable_dict()
    raw["targets"] = [
        {
            "name": "phone",
            "kind": "filesystem",
            "destination": "relative/device",
            "mode": "mirror",
        }
    ]

    with pytest.raises(PortableStateError, match="absolute"):
        parse_portable_dict(raw)


def test_portable_database_read_handles_question_mark_in_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "home?portable")
    initialize(settings.database_path)

    bundle = build_portable_bundle(settings)

    assert bundle.targets == ()
    assert settings.database_path.is_file()
    assert not (tmp_path / "home").exists()


def test_setup_redirect_only_updates_existing_spotify_config(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "home")
    settings.home.mkdir(parents=True)
    SpotifyOAuthConfig("existing-client", DEFAULT_REDIRECT_URI).save(
        settings.spotify_config_path
    )

    report = run_setup(
        settings,
        SetupOptions(spotify_redirect_uri="http://127.0.0.1:9999/callback"),
        which=lambda _: None,
    )
    saved = SpotifyOAuthConfig.load(settings.spotify_config_path)

    assert report.spotify_configured is True
    assert saved.client_id == "existing-client"
    assert saved.redirect_uri == "http://127.0.0.1:9999/callback"


def test_setup_preflights_invalid_backup_before_creating_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "new-home")

    with pytest.raises(SetupError, match="rclone remote path"):
        run_setup(
            settings,
            SetupOptions(
                source_priority="tidal,qobuz",
                backup_name="cloud",
                backup_destination="not-a-remote-path",
            ),
            which=lambda _: None,
        )

    assert not settings.home.exists()
    assert not settings.database_path.exists()


def test_doctor_reads_database_path_with_uri_reserved_characters(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "home?doctor#db")
    settings.library_dir.mkdir(parents=True)
    settings.playlists_dir.mkdir(parents=True)
    initialize(settings.database_path)

    report = run_doctor(
        settings,
        which=lambda _: None,
        token_loader=lambda _: None,
    )
    schema = next(check for check in report.checks if check.name == "schema")

    assert schema.status is CheckStatus.PASS
    assert settings.database_path.is_file()
    assert not (tmp_path / "home").exists()


def test_doctor_treats_missing_migration_tables_as_warning_for_old_schema(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "old-home")
    settings.library_dir.mkdir(parents=True)
    settings.playlists_dir.mkdir(parents=True)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(settings.database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '2');
            CREATE TABLE tracks (spotify_id TEXT PRIMARY KEY);
            CREATE TABLE playlists (spotify_id TEXT PRIMARY KEY);
            CREATE TABLE playlist_tracks (playlist_id TEXT, track_id TEXT, position INTEGER);
            CREATE TABLE sync_targets (
                id INTEGER PRIMARY KEY,
                name TEXT,
                kind TEXT,
                destination TEXT,
                mode TEXT
            );
            CREATE TABLE sync_runs (id INTEGER PRIMARY KEY);
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(
        settings,
        which=lambda _: None,
        token_loader=lambda _: None,
    )
    tables = next(check for check in report.checks if check.name == "tables")

    assert tables.status is CheckStatus.WARN
    assert "migration" in tables.message.lower()
