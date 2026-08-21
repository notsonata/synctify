from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

from synctify.backup import add_rclone_backup_target
from synctify.config import Settings
from synctify.db import initialize
from synctify.portable import (
    FORMAT_NAME,
    FORMAT_VERSION,
    PortableBundle,
    PortableStateError,
    PortableTarget,
    apply_import,
    build_portable_bundle,
    bundle_to_dict,
    parse_portable_dict,
    plan_import,
    write_portable_bundle,
)
from synctify.portable_cli import app
from synctify.spotify.auth import SpotifyOAuthConfig
from synctify.sync import add_filesystem_target
from synctify.user_config import load_user_config, set_user_config


def settings_for(tmp_path: Path, name: str = "home") -> Settings:
    home = tmp_path / name
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def test_export_contains_only_portable_setup_metadata(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    initialize(settings.database_path)
    set_user_config(settings.home, "source_priority", "tidal,qobuz")
    set_user_config(settings.home, "qobuz_quality", 7)
    SpotifyOAuthConfig("client-public", "http://127.0.0.1:8765/callback").save(
        settings.spotify_config_path
    )
    phone = tmp_path / "phone"
    phone.mkdir()
    with sqlite3.connect(settings.database_path) as connection:
        add_filesystem_target(connection, "phone", phone)
        add_rclone_backup_target(connection, "cloud", "pcloud:Synctify")

    bundle = build_portable_bundle(settings)
    data = bundle_to_dict(bundle)
    serialized = json.dumps(data)

    assert data["format"] == FORMAT_NAME
    assert data["format_version"] == FORMAT_VERSION
    assert data["config"] == {
        "qobuz_quality": 7,
        "source_priority": ["tidal", "qobuz"],
    }
    assert data["spotify"] == {
        "client_id": "client-public",
        "redirect_uri": "http://127.0.0.1:8765/callback",
    }
    assert {item["name"] for item in data["targets"]} == {"phone", "cloud"}
    for forbidden in (
        "access_token",
        "refresh_token",
        "expires_at",
        "local_path",
        "sha256",
        "spotify_last_pull_at",
        "track_resolutions",
        "playlist_tracks",
    ):
        assert forbidden not in serialized


def test_export_does_not_initialize_missing_home(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    destination = tmp_path / "portable.json"

    write_portable_bundle(settings, destination)

    assert destination.exists()
    assert settings.home.exists() is False
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert data["config"] == {}
    assert data["spotify"] is None
    assert data["targets"] == []


def test_import_preview_is_side_effect_free(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    bundle = PortableBundle(
        config={"qobuz_quality": 7},
        spotify={
            "client_id": "client-public",
            "redirect_uri": "http://127.0.0.1:8765/callback",
        },
        targets=(
            PortableTarget("cloud", "rclone", "pcloud:Synctify", "backup"),
        ),
    )

    plan = plan_import(settings, bundle)

    assert plan.apply is False
    assert plan.config_keys == ("qobuz_quality",)
    assert plan.spotify_action == "create"
    assert plan.targets[0].action == "create"
    assert settings.home.exists() is False


def test_apply_import_merges_config_and_creates_public_setup_state(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    initialize(settings.database_path)
    set_user_config(settings.home, "rclone", "/existing/rclone")

    bundle = PortableBundle(
        config={
            "source_priority": ("qobuz", "tidal"),
            "qobuz_quality": 6,
        },
        spotify={
            "client_id": "imported-client",
            "redirect_uri": "http://127.0.0.1:8765/callback",
        },
        targets=(
            PortableTarget("phone", "filesystem", "/Volumes/Phone/Music", "mirror"),
            PortableTarget("cloud", "rclone", "pcloud:Synctify", "backup"),
        ),
    )

    result = apply_import(settings, bundle)

    assert result.apply is True
    config = load_user_config(settings.home)
    assert config.rclone == "/existing/rclone"
    assert config.source_priority == ("qobuz", "tidal")
    assert config.qobuz_quality == 6
    spotify = SpotifyOAuthConfig.load(settings.spotify_config_path)
    assert spotify.client_id == "imported-client"
    with sqlite3.connect(settings.database_path) as connection:
        rows = connection.execute(
            "SELECT name, kind, destination, mode FROM sync_targets ORDER BY name"
        ).fetchall()
    assert rows == [
        ("cloud", "rclone", "pcloud:Synctify", "backup"),
        ("phone", "filesystem", "/Volumes/Phone/Music", "mirror"),
    ]


def test_target_conflict_aborts_before_config_write(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.ensure_directories()
    initialize(settings.database_path)
    existing = tmp_path / "existing-phone"
    existing.mkdir()
    with sqlite3.connect(settings.database_path) as connection:
        add_filesystem_target(connection, "phone", existing)

    bundle = PortableBundle(
        config={"qobuz_quality": 5},
        spotify=None,
        targets=(
            PortableTarget("phone", "filesystem", "/Volumes/OtherPhone", "mirror"),
        ),
    )

    with pytest.raises(PortableStateError, match="conflicts"):
        apply_import(settings, bundle)

    assert load_user_config(settings.home).qobuz_quality == 27
    assert not (settings.home / "config.json").exists()


def test_parser_rejects_unknown_format_version_and_invalid_target() -> None:
    with pytest.raises(PortableStateError, match="format version"):
        parse_portable_dict(
            {
                "format": FORMAT_NAME,
                "format_version": 999,
                "config": {},
                "spotify": None,
                "targets": [],
            }
        )

    with pytest.raises(PortableStateError, match="unsupported portable target"):
        parse_portable_dict(
            {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "config": {},
                "spotify": None,
                "targets": [
                    {
                        "name": "bad",
                        "kind": "filesystem",
                        "destination": "/tmp/bad",
                        "mode": "backup",
                    }
                ],
            }
        )


def test_cli_import_preview_does_not_create_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "new-home"
    source = tmp_path / "portable.json"
    source.write_text(
        json.dumps(
            {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "synctify_version": "0.19.0",
                "config": {"qobuz_quality": 7},
                "spotify": None,
                "targets": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = CliRunner().invoke(app, ["import", str(source)])

    assert result.exit_code == 0, result.output
    assert "Mode: preview" in result.output
    assert "No local state was changed" in result.output
    assert home.exists() is False


def test_cli_export_refuses_overwrite_without_force(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    destination = tmp_path / "portable.json"
    destination.write_text("existing\n", encoding="utf-8")
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = CliRunner().invoke(app, ["export", str(destination)])

    assert result.exit_code == 2
    assert "already exists" in result.output
    assert destination.read_text(encoding="utf-8") == "existing\n"
