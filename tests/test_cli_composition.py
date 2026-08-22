from __future__ import annotations

from pathlib import Path

import click
import typer
from typer.testing import CliRunner

from synctify.app import app
from synctify.migration_cli import app as legacy_app


def _command_tree(group: click.Group) -> dict[str, object]:
    tree: dict[str, object] = {}
    for name, command in sorted(group.commands.items()):
        if isinstance(command, click.Group):
            tree[name] = _command_tree(command)
        else:
            tree[name] = None
    return tree


def test_explicit_app_preserves_legacy_command_surface() -> None:
    explicit = typer.main.get_command(app)
    legacy = typer.main.get_command(legacy_app)
    assert isinstance(explicit, click.Group)
    assert isinstance(legacy, click.Group)

    assert _command_tree(explicit) == _command_tree(legacy)


def test_explicit_app_has_expected_top_level_and_nested_commands() -> None:
    command = typer.main.get_command(app)
    assert isinstance(command, click.Group)

    assert set(command.commands) == {
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
        "update",
    }

    expected_nested = {
        "spotify": {"login", "logout", "pull"},
        "resolve": {"auto", "clear", "set", "status"},
        "qobuz": {"doctor", "download-url"},
        "streamrip": {"doctor"},
        "playlists": {"build"},
        "targets": {"add", "add-backup", "list", "remove"},
        "config": {"set", "show", "unset"},
    }
    for group_name, names in expected_nested.items():
        child = command.commands[group_name]
        assert isinstance(child, click.Group)
        assert set(child.commands) == names


def test_all_help_paths_render_from_explicit_app() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    for group in ("spotify", "resolve", "qobuz", "streamrip", "playlists", "targets", "config"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, result.output


def test_installed_entry_point_targets_explicit_composition_module() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'synctify = "synctify.app:app"' in pyproject
    assert 'synctify = "synctify.migration_cli:app"' not in pyproject
