from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import httpx
import pytest
from typer.testing import CliRunner

import synctify.app as app_module
import synctify.self_update as self_update
from synctify.app import app
from synctify.self_update import AutomaticUpdateResult, ReleaseInfo, UpdateError


def _release_response(version: str = "1.0.6") -> dict[str, object]:
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": f"synctify-{version}-macos.zip",
                "url": "https://api.github.com/repos/notsonata/synctify/releases/assets/123",
                "size": 100,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }


def _client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[str | None], httpx.Client]:
    def factory(_token: str | None) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


def test_find_update_uses_latest_stable_macos_release_asset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/notsonata/synctify/releases/latest"
        return httpx.Response(200, json=_release_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        release = self_update.find_update("1.0.5", client)

    assert release is not None
    assert release.version == "1.0.6"
    assert release.tag == "v1.0.6"
    assert release.asset_name == "synctify-1.0.6-macos.zip"


def test_find_update_returns_none_when_current_is_latest() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_release_response("1.0.5"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert self_update.find_update("1.0.5", client) is None


def test_private_release_error_explains_github_authentication() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpdateError, match="gh auth login"):
            self_update.fetch_latest_release(client)


def test_automatic_update_checks_on_every_installed_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNCTIFY_INSTALLED", "1")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_release_response())

    factory = _client_factory(handler)
    first = self_update.run_automatic_update(
        "prompt",
        "1.0.5",
        token="token",
        client_factory=factory,
    )
    second = self_update.run_automatic_update(
        "prompt",
        "1.0.5",
        token="token",
        client_factory=factory,
    )

    assert calls == 2
    assert first.release is not None and first.release.version == "1.0.6"
    assert second.release is not None and second.release.version == "1.0.6"
    assert first.installed is False
    assert second.installed is False


def test_automatic_install_mode_installs_available_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNCTIFY_INSTALLED", "1")
    installed: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_release_response())

    def installer(release: ReleaseInfo, _client: httpx.Client) -> None:
        installed.append(release.version)

    result = self_update.run_automatic_update(
        "install",
        "1.0.5",
        token="token",
        client_factory=_client_factory(handler),
        installer=installer,
    )

    assert result.installed is True
    assert installed == ["1.0.6"]


def test_automatic_update_is_disabled_for_portable_or_development_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNCTIFY_INSTALLED", raising=False)
    called = False

    def factory(_token: str | None) -> httpx.Client:
        nonlocal called
        called = True
        raise AssertionError("network should not be used")

    result = self_update.run_automatic_update(
        "prompt",
        "1.0.5",
        client_factory=factory,
    )

    assert result.checked is False
    assert called is False


def test_noninteractive_cli_notifies_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNCTIFY_INSTALLED", "1")
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    release = ReleaseInfo(
        version="1.0.6",
        tag="v1.0.6",
        asset_name="synctify-1.0.6-macos.zip",
        asset_api_url="https://api.github.com/assets/123",
    )
    monkeypatch.setattr(app_module, "_interactive_terminal", lambda: False)
    monkeypatch.setattr(
        app_module,
        "run_automatic_update",
        lambda mode, current: AutomaticUpdateResult(checked=True, release=release),
    )

    def fail_confirm(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("non-interactive invocation must not prompt")

    monkeypatch.setattr(app_module.typer, "confirm", fail_confirm)
    result = CliRunner().invoke(app, ["status"])

    assert "Synctify 1.0.6 is available" in result.output
    assert "Run `synctify self-update`" in result.output


def test_interactive_default_policy_prompts_before_installing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SYNCTIFY_INSTALLED", "1")
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path))
    release = ReleaseInfo(
        version="1.0.6",
        tag="v1.0.6",
        asset_name="synctify-1.0.6-macos.zip",
        asset_api_url="https://api.github.com/assets/123",
    )
    monkeypatch.setattr(app_module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        app_module,
        "run_automatic_update",
        lambda mode, current: AutomaticUpdateResult(checked=True, release=release),
    )
    prompts: list[str] = []

    def decline(message: str, *, default: bool) -> bool:
        prompts.append(message)
        assert default is True
        return False

    monkeypatch.setattr(app_module.typer, "confirm", decline)
    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 1
    assert prompts == ["Synctify 1.0.6 is available (current: 1.0.5). Update now?"]
