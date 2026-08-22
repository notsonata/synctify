from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, TabbedContent

from synctify.config import Settings
from synctify.db import connect, initialize
from synctify.tui import SynctifyTUI
from synctify.tui_backend import (
    list_unresolved_tracks,
    read_dashboard,
    run_cli_command,
    set_manual_resolution,
)


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def _insert_desired_track(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO tracks(
                spotify_id, title, artist, album, isrc, duration_ms, status
            ) VALUES ('spotify-1', 'Paranoid Android', 'Radiohead', 'OK Computer',
                      'GBAYE9701376', 386000, 'unresolved')
            """
        )
        connection.execute(
            """
            INSERT INTO playlists(spotify_id, name, source_kind, collaborative)
            VALUES ('playlist-1', 'Favorites', 'playlist', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO playlist_tracks(playlist_id, track_id, position)
            VALUES ('playlist-1', 'spotify-1', 0)
            """
        )


def test_dashboard_is_read_only_when_uninitialized(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    state = read_dashboard(settings)

    assert not state.initialized
    assert state.tracks == 0
    assert not settings.home.exists()


def test_tui_backend_lists_and_resolves_desired_track(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _insert_desired_track(settings)

    state = read_dashboard(settings)
    unresolved = list_unresolved_tracks(settings)

    assert state.initialized
    assert state.tracks == 1
    assert state.unresolved == 1
    assert state.resolutions == 0
    assert [item.spotify_id for item in unresolved] == ["spotify-1"]

    set_manual_resolution(settings, "spotify-1", "QOBUZ", "qobuz-track-1")

    after = read_dashboard(settings)
    assert after.resolutions == 1
    assert after.pending_downloads == 1
    assert list_unresolved_tracks(settings) == ()
    with connect(settings.database_path) as connection:
        row = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()
    assert row["provider"] == "qobuz"
    assert row["provider_track_id"] == "qobuz-track-1"


def test_cli_command_runner_uses_existing_app_and_streams_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    seen: dict[str, object] = {}

    class FakeProcess:
        stdout = iter(["first line\n", "second line\n"])

        def wait(self) -> int:
            return 0

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("synctify.tui_backend.subprocess.Popen", fake_popen)
    output: list[str] = []

    result = run_cli_command(
        settings,
        ("spotify", "pull"),
        on_output=output.append,
    )

    assert result.returncode == 0
    assert result.args == ("spotify", "pull")
    assert seen["command"][-4:] == ["-m", "synctify.app", "spotify", "pull"]
    environment = seen["kwargs"]["env"]
    assert environment["SYNCTIFY_HOME"] == str(settings.home)
    assert environment["SYNCTIFY_SKIP_AUTO_UPDATE"] == "1"
    assert output == ["first line", "second line"]


def test_cli_command_runner_rejects_nested_tui(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nested"):
        run_cli_command(_settings(tmp_path), ("tui",))


def test_tui_mounts_and_keyboard_tabs_work(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        app = SynctifyTUI(settings=settings)
        async with app.run_test(size=(140, 44)) as pilot:
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "dashboard"
            assert app.query_one("#unresolved-table", DataTable).row_count == 0

            await pilot.press("2")
            await pilot.pause()
            assert tabs.active == "resolver"

            await pilot.press("3")
            await pilot.pause()
            assert tabs.active == "doctor"

            await pilot.press("4")
            await pilot.pause()
            assert tabs.active == "audit"

            await pilot.press("5")
            await pilot.pause()
            assert tabs.active == "commands"
            assert app.query_one("#command-input", Input)

    asyncio.run(exercise())
