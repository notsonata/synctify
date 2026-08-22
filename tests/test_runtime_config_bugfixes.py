from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from synctify.entrypoint import app
from synctify.user_config import effective_user_config, set_user_config


runner = CliRunner()


def test_effective_config_applies_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = set_user_config(tmp_path, "qobuz-quality", "27")
    monkeypatch.setenv("SYNCTIFY_SOURCES", "tidal,qobuz")
    monkeypatch.setenv("SYNCTIFY_QOBUZ_DL", "/env/qobuz-dl")
    monkeypatch.setenv("SYNCTIFY_STREAMRIP", "/env/rip")
    monkeypatch.setenv("SYNCTIFY_RCLONE", "/env/rclone")
    monkeypatch.setenv("SYNCTIFY_QOBUZ_QUALITY", "7")
    monkeypatch.setenv("SYNCTIFY_STREAMRIP_TIDAL_QUALITY", "1")

    effective = effective_user_config(config)

    assert effective.source_priority == ("tidal", "qobuz")
    assert effective.qobuz_dl == "/env/qobuz-dl"
    assert effective.streamrip == "/env/rip"
    assert effective.rclone == "/env/rclone"
    assert effective.qobuz_quality == 7
    assert effective.streamrip_tidal_quality == 1


def test_config_show_reports_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    set_user_config(tmp_path, "qobuz-quality", "27")
    set_user_config(tmp_path, "source-priority", "qobuz,tidal")
    monkeypatch.setenv("SYNCTIFY_QOBUZ_QUALITY", "7")
    monkeypatch.setenv("SYNCTIFY_SOURCES", "tidal,qobuz")

    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0
    assert "qobuz-quality: 7" in result.stdout
    assert "source-priority: tidal,qobuz" in result.stdout


def test_config_show_invalid_quality_env_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    monkeypatch.setenv("SYNCTIFY_QOBUZ_QUALITY", "999")

    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 2
    assert "qobuz_quality must be one of" in result.output


def test_acquire_invalid_explicit_qobuz_quality_exits_before_base_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = runner.invoke(
        app,
        ["acquire", "--source", "qobuz", "--quality", "999", "--dry-run"],
    )

    assert result.exit_code == 2
    assert "qobuz_quality must be one of" in result.output
    assert not home.exists()


def test_acquire_invalid_streamrip_quality_env_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setenv("SYNCTIFY_STREAMRIP_TIDAL_QUALITY", "99")

    result = runner.invoke(app, ["acquire", "--source", "tidal", "--dry-run"])

    assert result.exit_code == 2
    assert "streamrip quality must be one of" in result.output
    assert not home.exists()


def test_update_invalid_source_environment_exits_before_creating_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setenv("SYNCTIFY_SOURCES", "bogus")

    result = runner.invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 2
    assert "unsupported source" in result.output
    assert not home.exists()
