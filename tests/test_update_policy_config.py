from __future__ import annotations

from pathlib import Path

import pytest

from synctify.user_config import (
    UserConfig,
    UserConfigError,
    load_user_config,
    resolve_auto_update,
    set_user_config,
)


def test_default_update_policy_prompts_on_every_installed_run(tmp_path: Path) -> None:
    config = load_user_config(tmp_path)

    assert config.auto_update == "prompt"
    assert resolve_auto_update(config) == "prompt"


def test_update_policy_can_be_changed_or_overridden(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = set_user_config(tmp_path, "auto-update", "off")
    assert config.auto_update == "off"
    assert resolve_auto_update(config) == "off"

    monkeypatch.setenv("SYNCTIFY_AUTO_UPDATE", "install")
    assert resolve_auto_update(config) == "install"
    assert resolve_auto_update(config, "check") == "check"


def test_invalid_update_policy_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UserConfigError, match="auto_update must be one of"):
        set_user_config(tmp_path, "auto-update", "weekly")

    with pytest.raises(UserConfigError, match="auto_update must be one of"):
        resolve_auto_update(UserConfig(), "sometimes")
