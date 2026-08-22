from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import (
    Button,
    DataTable,
    Input,
    LoadingIndicator,
    ProgressBar,
    Static,
    TabbedContent,
)

from synctify.config import Settings
from synctify.db import connect, initialize
from synctify.spotify.selection import PlaylistCatalogEntry, store_playlist_catalog
from synctify.tui import SynctifyTUI
from synctify.tui_backend import (
    list_unresolved_tracks,
    read_dashboard,
    read_spotify_playlists,
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


def _patch_fake_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSelector:
        def register(self, fileobj, _events):
            self.fileobj = fileobj

        def select(self, timeout=0):
            del timeout
            if getattr(self.fileobj, "lines", None):
                return [(SimpleNamespace(fileobj=self.fileobj), 1)]
            return []

        def close(self):
            return None

    monkeypatch.setattr("synctify.tui_backend.selectors.DefaultSelector", FakeSelector)


def test_cli_command_runner_uses_existing_app_and_streams_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    seen: dict[str, object] = {}
    _patch_fake_selector(monkeypatch)

    class FakeStdout:
        def __init__(self):
            self.lines = ["first line\n", "second line\n"]

        def readline(self):
            return self.lines.pop(0) if self.lines else ""

        def __iter__(self):
            while self.lines:
                yield self.lines.pop(0)

        def close(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()

        def poll(self):
            return 0 if not self.stdout.lines else None

        def wait(self, timeout=None):
            del timeout
            return 0

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("synctify.tui_backend.subprocess.Popen", fake_popen)
    output: list[str] = []

    result = run_cli_command(
        settings,
        ("spotify", "fetch-playlists"),
        on_output=output.append,
    )

    assert result.returncode == 0
    assert not result.cancelled
    assert result.args == ("spotify", "fetch-playlists")
    assert seen["command"][-4:] == ["-m", "synctify.app", "spotify", "fetch-playlists"]
    environment = seen["kwargs"]["env"]
    assert environment["SYNCTIFY_HOME"] == str(settings.home)
    assert environment["SYNCTIFY_LIBRARY_DIR"] == str(settings.library_dir)
    assert environment["SYNCTIFY_SKIP_AUTO_UPDATE"] == "1"
    assert output == ["first line", "second line"]


def test_cli_command_runner_terminates_child_when_output_callback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _patch_fake_selector(monkeypatch)

    class FakeStdout:
        lines = ["still running\n"]

        def readline(self):
            return self.lines.pop(0) if self.lines else ""

        def __iter__(self):
            return iter(())

        def close(self):
            return None

    class FakeProcess:
        stdout = FakeStdout()
        terminated = False
        waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            self.waited = True
            return -15

    process = FakeProcess()
    monkeypatch.setattr("synctify.tui_backend.subprocess.Popen", lambda *args, **kwargs: process)

    def fail_output(_line: str) -> None:
        raise RuntimeError("UI closed")

    with pytest.raises(RuntimeError, match="UI closed"):
        run_cli_command(settings, ("update",), on_output=fail_output)

    assert process.terminated
    assert process.waited


def test_cli_command_runner_cancels_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _patch_fake_selector(monkeypatch)

    class FakeStdout:
        lines: list[str] = []

        def readline(self):
            return ""

        def __iter__(self):
            return iter(())

        def close(self):
            return None

    class FakeProcess:
        stdout = FakeStdout()
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            return -15

    process = FakeProcess()
    monkeypatch.setattr("synctify.tui_backend.subprocess.Popen", lambda *args, **kwargs: process)

    result = run_cli_command(settings, ("update",), cancelled=lambda: True)

    assert result.cancelled
    assert process.terminated


def test_cli_command_runner_rejects_nested_tui(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nested"):
        run_cli_command(_settings(tmp_path), ("tui",))


def test_tui_reads_cached_spotify_catalog(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        store_playlist_catalog(
            connection,
            "user-1",
            (
                PlaylistCatalogEntry(
                    "synctify:spotify:liked-songs",
                    "Liked Songs",
                    "liked",
                    None,
                    "user-1",
                    False,
                    None,
                ),
            ),
        )

    entries = read_spotify_playlists(settings)
    assert len(entries) == 1
    assert entries[0].name == "Liked Songs"
    assert not entries[0].tracked


def test_tui_mounts_spotify_tab_busy_indicator_and_keyboard_tabs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        app = SynctifyTUI(settings=settings)
        async with app.run_test(size=(150, 48)) as pilot:
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "dashboard"
            assert app.query_one("#unresolved-table", DataTable).row_count == 0
            assert app.query_one("#busy-indicator", LoadingIndicator).display is False
            assert app.query_one("#busy-progress", ProgressBar).display is False
            assert app.query_one("#cancel-action", Button).disabled is True

            await pilot.press("2")
            await pilot.pause()
            assert tabs.active == "spotify"
            assert app.query_one("#spotify-playlists", DataTable)
            assert app.query_one("#spotify-tracks", DataTable)

            await pilot.press("3")
            await pilot.pause()
            assert tabs.active == "resolver"

            await pilot.press("4")
            await pilot.pause()
            assert tabs.active == "doctor"

            await pilot.press("5")
            await pilot.pause()
            assert tabs.active == "audit"

            await pilot.press("6")
            await pilot.pause()
            assert tabs.active == "commands"
            assert app.query_one("#command-input", Input)

    asyncio.run(exercise())


def test_tui_renders_resolution_progress_in_operation_strip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def exercise() -> None:
        app = SynctifyTUI(settings=settings)
        async with app.run_test(size=(150, 48)) as pilot:
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "dashboard"
            assert app._set_busy("Library update")

            app._append_command_output(
                "[update] Resolving via qobuz [23/100] Radiohead - Paranoid Android"
            )
            await pilot.pause()

            progress = app.query_one("#busy-progress", ProgressBar)
            indicator = app.query_one("#busy-indicator", LoadingIndicator)
            label = app.query_one("#busy-label", Static)
            assert tabs.active == "dashboard"
            assert progress.display is True
            assert indicator.display is False
            rendered = str(label.render())
            assert "Qobuz resolution 23/100" in rendered
            assert "Radiohead - Paranoid Android" in rendered

            app._append_command_output("[update] Planning downloads...")
            await pilot.pause()
            assert progress.display is False
            assert indicator.display is True
            assert "Planning downloads" in str(label.render())

            app._clear_busy("done")

    asyncio.run(exercise())
