from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import synctify.entrypoint as entrypoint
from synctify.entrypoint import app
from synctify.providers.qobuz import QobuzDLProvider
from synctify.providers.streamrip import StreamripProvider
from synctify.user_config import (
    UserConfig,
    UserConfigError,
    config_path,
    load_user_config,
    resolve_qobuz_dl,
    resolve_qobuz_quality,
    resolve_rclone,
    resolve_source_priority,
    resolve_streamrip,
    resolve_streamrip_quality,
    set_user_config,
    unset_user_config,
)


def test_missing_config_uses_built_in_defaults(tmp_path: Path) -> None:
    config = load_user_config(tmp_path)

    assert config.source_priority == ("qobuz", "tidal", "deezer")
    assert config.qobuz_dl == "qobuz-dl"
    assert config.streamrip == "rip"
    assert config.rclone == "rclone"
    assert config.qobuz_quality == 27
    assert config.streamrip_quality_for("tidal") == 3
    assert config_path(tmp_path).exists() is False


def test_set_and_unset_store_only_explicit_overrides(tmp_path: Path) -> None:
    set_user_config(tmp_path, "qobuz-quality", "7")
    set_user_config(tmp_path, "source-priority", "qobuz,tidal")

    raw = json.loads(config_path(tmp_path).read_text(encoding="utf-8"))
    assert raw == {
        "qobuz_quality": 7,
        "source_priority": ["qobuz", "tidal"],
    }

    config, existed = unset_user_config(tmp_path, "qobuz-quality")
    assert existed is True
    assert config.qobuz_quality == 27
    raw = json.loads(config_path(tmp_path).read_text(encoding="utf-8"))
    assert raw == {"source_priority": ["qobuz", "tidal"]}


def test_invalid_config_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="duplicates"):
        set_user_config(tmp_path, "source-priority", "qobuz,qobuz")
    with pytest.raises(UserConfigError, match="unsupported source"):
        set_user_config(tmp_path, "source-priority", "soundcloud,qobuz")
    with pytest.raises(UserConfigError, match="must be one of"):
        set_user_config(tmp_path, "qobuz-quality", "5")
    with pytest.raises(UserConfigError, match="must be one of"):
        set_user_config(tmp_path, "qobuz-quality", "4")
    with pytest.raises(UserConfigError, match="unknown config key"):
        set_user_config(tmp_path, "nope", "value")


def test_legacy_lossy_settings_remain_readable_but_resolve_losslessly(tmp_path: Path) -> None:
    config_path(tmp_path).write_text(
        json.dumps(
            {
                "source_priority": ["soundcloud", "qobuz", "tidal"],
                "qobuz_quality": 5,
            }
        ),
        encoding="utf-8",
    )

    config = load_user_config(tmp_path)

    assert config.source_priority == ("qobuz", "tidal")
    assert config.qobuz_quality == 6
    # Loading compatibility does not silently rewrite the user's saved file.
    raw = json.loads(config_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["source_priority"] == ["soundcloud", "qobuz", "tidal"]
    assert raw["qobuz_quality"] == 5


def test_precedence_cli_then_environment_then_saved_then_builtin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = set_user_config(tmp_path, "qobuz-dl", "/saved/qobuz-dl")
    config = set_user_config(tmp_path, "streamrip", "/saved/rip")
    config = set_user_config(tmp_path, "rclone", "/saved/rclone")
    config = set_user_config(tmp_path, "source-priority", "tidal,qobuz")
    config = set_user_config(tmp_path, "qobuz-quality", "7")
    config = set_user_config(tmp_path, "streamrip-tidal-quality", "2")

    assert resolve_qobuz_dl(config) == "/saved/qobuz-dl"
    assert resolve_streamrip(config) == "/saved/rip"
    assert resolve_rclone(config) == "/saved/rclone"
    assert resolve_source_priority(config) == ("tidal", "qobuz")
    assert resolve_qobuz_quality(config) == 7
    assert resolve_streamrip_quality(config, "tidal") == 2

    monkeypatch.setenv("SYNCTIFY_QOBUZ_DL", "/env/qobuz-dl")
    monkeypatch.setenv("SYNCTIFY_STREAMRIP", "/env/rip")
    monkeypatch.setenv("SYNCTIFY_RCLONE", "/env/rclone")
    monkeypatch.setenv("SYNCTIFY_SOURCES", "deezer,tidal")
    monkeypatch.setenv("SYNCTIFY_QOBUZ_QUALITY", "6")
    monkeypatch.setenv("SYNCTIFY_STREAMRIP_TIDAL_QUALITY", "1")

    assert resolve_qobuz_dl(config) == "/env/qobuz-dl"
    assert resolve_streamrip(config) == "/env/rip"
    assert resolve_rclone(config) == "/env/rclone"
    assert resolve_source_priority(config) == ("deezer", "tidal")
    assert resolve_qobuz_quality(config) == 6
    assert resolve_streamrip_quality(config, "tidal") == 1

    assert resolve_qobuz_dl(config, "/cli/qobuz-dl") == "/cli/qobuz-dl"
    assert resolve_streamrip(config, "/cli/rip") == "/cli/rip"
    assert resolve_rclone(config, "/cli/rclone") == "/cli/rclone"
    assert resolve_source_priority(config, "qobuz,deezer") == ("qobuz", "deezer")
    with pytest.raises(UserConfigError, match="unsupported source"):
        resolve_source_priority(config, "soundcloud,qobuz")
    with pytest.raises(UserConfigError, match="must be one of"):
        resolve_qobuz_quality(config, 5)
    assert resolve_streamrip_quality(config, "tidal", 4) == 4


def test_config_cli_set_show_and_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    runner = CliRunner()

    result = runner.invoke(app, ["config", "set", "source-priority", "tidal,qobuz"])
    assert result.exit_code == 0
    assert "source-priority: tidal,qobuz" in result.stdout

    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert str(config_path(tmp_path)) in result.stdout
    assert "source-priority: tidal,qobuz" in result.stdout

    result = runner.invoke(app, ["config", "unset", "source-priority"])
    assert result.exit_code == 0
    assert "source-priority: qobuz,tidal,deezer" in result.stdout


def test_configured_acquire_passes_saved_defaults_to_existing_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    set_user_config(tmp_path, "qobuz-dl", "/saved/qobuz-dl")
    set_user_config(tmp_path, "qobuz-quality", "7")
    captured: dict[str, object] = {}

    def fake_acquire(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(entrypoint, "base_acquire", fake_acquire)
    result = CliRunner().invoke(app, ["acquire", "--source", "qobuz", "--dry-run"])

    assert result.exit_code == 0
    assert captured["source"] == "qobuz"
    assert captured["quality"] == 7
    assert captured["qobuz_dl"] == "/saved/qobuz-dl"


def test_configured_update_provider_uses_saved_qualities() -> None:
    config = UserConfig(
        qobuz_dl="/saved/qobuz-dl",
        streamrip="/saved/rip",
        qobuz_quality=7,
        streamrip_tidal_quality=1,
    )

    qobuz = entrypoint._configured_update_provider(
        config,
        "qobuz",
        config.qobuz_dl,
        config.streamrip,
    )
    tidal = entrypoint._configured_update_provider(
        config,
        "tidal",
        config.qobuz_dl,
        config.streamrip,
    )

    assert isinstance(qobuz, QobuzDLProvider)
    assert qobuz.config.executable == "/saved/qobuz-dl"
    assert qobuz.config.quality == 7
    assert isinstance(tidal, StreamripProvider)
    assert tidal.config.executable == "/saved/rip"
    assert tidal.config.quality == 1
