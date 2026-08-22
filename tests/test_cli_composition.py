from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from typer.testing import CliRunner

from synctify.app import app
from synctify.db import connect, initialize
from synctify.migration_cli import app as legacy_app


def _commands(group: Any) -> dict[str, Any]:
    commands = getattr(group, "commands", None)
    assert isinstance(commands, dict)
    return commands


def _command_tree(group: Any) -> dict[str, object]:
    tree: dict[str, object] = {}
    for name, command in sorted(_commands(group).items()):
        child_commands = getattr(command, "commands", None)
        tree[name] = _command_tree(command) if isinstance(child_commands, dict) else None
    return tree


def test_explicit_app_preserves_legacy_surface_except_intentional_public_commands() -> None:
    explicit = _command_tree(typer.main.get_command(app))
    legacy = _command_tree(typer.main.get_command(legacy_app))

    assert explicit.pop("tui") is None
    assert explicit.pop("upgrade") is None
    # Spotify selection intentionally replaces legacy all-at-once `pull`.
    explicit_spotify = explicit["spotify"]
    legacy_spotify = legacy["spotify"]
    assert isinstance(explicit_spotify, dict)
    assert isinstance(legacy_spotify, dict)
    assert explicit_spotify == {
        "fetch-playlists": None,
        "login": None,
        "logout": None,
        "update-tracked": None,
    }
    legacy["spotify"] = {
        name: value for name, value in legacy_spotify.items() if name != "pull"
    }
    explicit["spotify"] = {
        name: value
        for name, value in explicit_spotify.items()
        if name not in {"fetch-playlists", "update-tracked"}
    }
    assert explicit == legacy


def test_explicit_app_has_expected_top_level_and_nested_commands() -> None:
    command = typer.main.get_command(app)
    commands = _commands(command)

    assert set(commands) == {
        "acquire",
        "audit",
        "backup",
        "clean",
        "config",
        "doctor",
        "export",
        "import",
        "init",
        "migrate",
        "playlists",
        "qobuz",
        "relink",
        "resolve",
        "setup",
        "spotify",
        "status",
        "streamrip",
        "sync",
        "targets",
        "tui",
        "update",
        "upgrade",
    }

    expected_nested = {
        "spotify": {"login", "logout", "fetch-playlists", "update-tracked"},
        "resolve": {"auto", "clear", "set", "status"},
        "qobuz": {"doctor", "download-url"},
        "streamrip": {"doctor"},
        "playlists": {"build"},
        "targets": {"add", "add-backup", "list", "remove"},
        "config": {"set", "show", "unset"},
    }
    for group_name, names in expected_nested.items():
        assert set(_commands(commands[group_name])) == names


def test_all_help_paths_render_from_explicit_app() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["tui", "--help"]).exit_code == 0
    assert runner.invoke(app, ["upgrade", "--help"]).exit_code == 0
    assert runner.invoke(app, ["spotify", "fetch-playlists", "--help"]).exit_code == 0
    assert runner.invoke(app, ["spotify", "update-tracked", "--help"]).exit_code == 0
    removed = runner.invoke(app, ["spotify", "pull", "--help"])
    assert removed.exit_code != 0
    assert "No such command 'pull'" in removed.output
    for group in ("spotify", "resolve", "qobuz", "streamrip", "playlists", "targets", "config"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, result.output


def test_explicit_resolve_set_reports_unsupported_provider_cleanly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    initialize(home / "synctify.sqlite3")
    with connect(home / "synctify.sqlite3") as connection:
        connection.execute(
            """
            INSERT INTO tracks(spotify_id, title, artist, album, isrc, duration_ms)
            VALUES ('spotify-1', 'Song', 'Artist', 'Album', 'USAAA2600001', 180000)
            """
        )

    result = CliRunner().invoke(
        app,
        ["resolve", "set", "spotify-1", "soundcloud", "legacy-id"],
    )

    assert result.exit_code == 2
    assert "unsupported resolution provider" in result.output
    assert "Traceback" not in result.output


def test_installed_entry_point_targets_explicit_composition_module() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'synctify = "synctify.app:app"' in pyproject
    assert 'synctify = "synctify.migration_cli:app"' not in pyproject
