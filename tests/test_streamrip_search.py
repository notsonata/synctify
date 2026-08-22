from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import pytest

from synctify.models import Track
from synctify.providers.streamrip_search import (
    StreamripCatalogSearch,
    StreamripSearchConfig,
    StreamripSearchError,
)


def _track(**overrides: object) -> Track:
    values = {
        "spotify_id": "spotify-1",
        "title": "Paranoid Android",
        "artist": "Radiohead",
        "album": "OK Computer",
        "isrc": "GBAYE9701376",
        "duration_ms": 386_000,
    }
    values.update(overrides)
    return Track(**values)  # type: ignore[arg-type]


def _write_tidal_config(
    path: Path,
    *,
    access_token: str = "access",
    refresh_token: str = "refresh",
    token_expiry: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expiry = time.time() + 3600 if token_expiry is None else token_expiry
    path.write_text(
        "\n".join(
            [
                "[tidal]",
                f'access_token = "{access_token}"',
                f'refresh_token = "{refresh_token}"',
                f'token_expiry = "{expiry}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_build_search_command_is_non_shell_and_source_explicit(tmp_path: Path) -> None:
    search = StreamripCatalogSearch(StreamripSearchConfig(executable="/opt/homebrew/bin/rip"))
    output = tmp_path / "results.json"

    command = search.build_search_command("qobuz", "Radiohead Paranoid Android", output, limit=8)

    assert command == [
        "/opt/homebrew/bin/rip",
        "--no-db",
        "--no-progress",
        "search",
        "--output-file",
        str(output),
        "--num-results",
        "8",
        "qobuz",
        "track",
        "Radiohead Paranoid Android",
    ]


def test_search_uses_isrc_and_metadata_queries_and_returns_shared_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    output_paths: list[Path] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = Path(command[command.index("--output-file") + 1])
        output_paths.append(output)
        output.write_text(
            json.dumps(
                [
                    {
                        "source": "qobuz",
                        "media_type": "track",
                        "id": "12345",
                        "desc": "Paranoid Android by Radiohead",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(runner=runner)

    candidates = tuple(search.search(_track(), "qobuz", limit=5))

    assert len(commands) == 2
    assert commands[0][-1] == "GBAYE9701376"
    assert commands[1][-1] == "Radiohead Paranoid Android"
    assert len(candidates) == 1
    assert candidates[0].provider == "qobuz"
    assert candidates[0].provider_track_id == "12345"
    assert candidates[0].title == "Paranoid Android"
    assert candidates[0].artist == "Radiohead"
    assert all(not path.exists() for path in output_paths)


def test_search_rejects_candidate_not_returned_by_both_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        output = Path(command[command.index("--output-file") + 1])
        candidate_id = "isrc-result" if calls == 1 else "metadata-result"
        output.write_text(
            json.dumps(
                [
                    {
                        "source": "qobuz",
                        "media_type": "track",
                        "id": candidate_id,
                        "desc": "Paranoid Android by Radiohead",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")

    candidates = tuple(StreamripCatalogSearch(runner=runner).search(_track(), "qobuz"))

    assert calls == 2
    assert candidates == ()


def test_search_without_spotify_isrc_refuses_sparse_metadata_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("Streamrip should not be queried without ISRC identity evidence")

    candidates = tuple(
        StreamripCatalogSearch(runner=runner).search(_track(isrc=None), "qobuz")
    )

    assert candidates == ()


def test_search_ignores_invalid_result_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output-file") + 1])
        output.write_text(
            json.dumps(
                [
                    {"source": "tidal", "media_type": "track", "id": "wrong-source", "desc": "Song by Artist"},
                    {"source": "qobuz", "media_type": "album", "id": "wrong-type", "desc": "Album by Artist"},
                    {"source": "qobuz", "media_type": "track", "id": "bad-desc", "desc": "No separator"},
                    {"source": "qobuz", "media_type": "track", "id": "good", "desc": "Song by Artist"},
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(runner=runner)

    candidates = tuple(search.search(_track(title="Song", artist="Artist"), "qobuz"))

    assert [candidate.provider_track_id for candidate in candidates] == ["good"]


def test_search_surfaces_streamrip_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="login required")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(runner=runner)

    with pytest.raises(StreamripSearchError, match="login required"):
        search.search(_track(), "qobuz")


def test_tidal_search_refuses_missing_session_before_launching_streamrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("Streamrip must not start an interactive Tidal login")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(
        StreamripSearchConfig(config_path=tmp_path / "missing.toml"),
        runner=runner,
    )

    with pytest.raises(StreamripSearchError, match="not configured"):
        search.search(_track(), "tidal")

    assert called is False


def test_tidal_search_refuses_expired_session_before_launching_streamrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    _write_tidal_config(config_path, token_expiry=time.time() - 60)
    called = False

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("Streamrip must not start an interactive Tidal login")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(
        StreamripSearchConfig(config_path=config_path),
        runner=runner,
    )

    with pytest.raises(StreamripSearchError, match="missing or expired"):
        search.search(_track(), "tidal")

    assert called is False


def test_tidal_search_uses_pre_authenticated_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    _write_tidal_config(config_path)
    commands: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = Path(command[command.index("--output-file") + 1])
        output.write_text(
            json.dumps(
                [
                    {
                        "source": "tidal",
                        "media_type": "track",
                        "id": "tidal-123",
                        "desc": "Paranoid Android by Radiohead",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("synctify.providers.streamrip_search.shutil.which", lambda _: "/usr/bin/rip")
    search = StreamripCatalogSearch(
        StreamripSearchConfig(config_path=config_path),
        runner=runner,
    )

    candidates = tuple(search.search(_track(), "tidal"))

    assert len(commands) == 2
    assert [candidate.provider_track_id for candidate in candidates] == ["tidal-123"]


def test_search_rejects_unsupported_source_before_running() -> None:
    search = StreamripCatalogSearch()

    with pytest.raises(ValueError, match="unsupported"):
        search.search(_track(), "spotify")
