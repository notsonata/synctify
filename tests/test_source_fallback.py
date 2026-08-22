from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from synctify.cli_entry import app
from synctify.db import connect, initialize
from synctify.providers.base import AcquiredTrack
from synctify.resolution import Candidate
from synctify.spotify.ingest import (
    PlaylistEntry,
    SpotifyPlaylist,
    SpotifySnapshot,
    SpotifyTrack,
)
from synctify.workflow import preview_update_workflow, run_update_workflow


class FallbackSearch:
    name = "fallback-search"
    supported_sources = frozenset({"qobuz", "tidal", "deezer", "soundcloud"})

    def __init__(self, behavior: dict[str, str]) -> None:
        self.behavior = behavior
        self.calls: list[tuple[str, str]] = []

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def search(self, track, source: str, *, limit: int | None = None):
        self.calls.append((track.spotify_id, source))
        behavior = self.behavior.get(source, "none")
        if behavior == "error":
            raise RuntimeError(f"{source} unavailable")
        if behavior == "none":
            return []
        if behavior == "ambiguous":
            return [
                Candidate(source, f"{source}-a", track.title, track.artist),
                Candidate(source, f"{source}-b", track.title, track.artist),
            ]
        if behavior == "match":
            return [Candidate(source, f"{source}-match", track.title, track.artist)]
        raise AssertionError(behavior)


class FakeDownloader:
    supported_sources = frozenset({"qobuz", "tidal", "deezer", "soundcloud"})

    def __init__(self, source: str) -> None:
        self.name = "qobuz-dl" if source == "qobuz" else "streamrip"

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        path = destination / candidate.provider / f"{candidate.provider_track_id}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(candidate.provider_track_id.encode())
        return AcquiredTrack(candidate.provider, candidate.provider_track_id, path)


def _snapshot(track_ids: tuple[str, ...] = ("spotify-1",)) -> SpotifySnapshot:
    entries = []
    for index, spotify_id in enumerate(track_ids):
        entries.append(
            PlaylistEntry(
                SpotifyTrack(
                    spotify_id=spotify_id,
                    title=f"Track {index + 1}",
                    artist="Artist",
                    album="Album",
                    duration_ms=200_000 + index,
                    isrc=f"USAAA26000{index}",
                )
            )
        )
    return SpotifySnapshot(
        "user-1",
        (
            SpotifyPlaylist(
                spotify_id="playlist-1",
                name="Playlist",
                tracks=tuple(entries),
                snapshot_id="snapshot-1",
                source_kind="playlist",
                owner_id="user-1",
            ),
        ),
    )


def test_unresolved_qobuz_falls_through_to_tidal(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FallbackSearch({"qobuz": "none", "tidal": "match"})

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            search,
            ("qobuz", "tidal", "deezer"),
            lambda source: FakeDownloader(source),
            tmp_path / "library",
            tmp_path / "playlists",
        )
        row = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert [item.source for item in report.resolutions] == ["qobuz", "tidal"]
    assert row["provider"] == "tidal"
    assert row["provider_track_id"] == "tidal-match"
    assert search.calls == [("spotify-1", "qobuz"), ("spotify-1", "tidal")]


def test_ambiguous_result_falls_through_to_next_source(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FallbackSearch({"qobuz": "ambiguous", "tidal": "match"})

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            search,
            ("qobuz", "tidal"),
            lambda source: FakeDownloader(source),
            tmp_path / "library",
            tmp_path / "playlists",
        )

    assert report.resolutions[0].ambiguous == 1
    assert report.resolutions[1].resolved == 1
    assert report.operational_failures == 0


def test_recovered_search_error_does_not_fail_workflow(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FallbackSearch({"qobuz": "error", "tidal": "match"})

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            search,
            ("qobuz", "tidal"),
            lambda source: FakeDownloader(source),
            tmp_path / "library",
            tmp_path / "playlists",
        )

    assert report.resolutions[0].failed == 1
    assert report.resolutions[1].resolved == 1
    assert report.resolution_failures == 0
    assert report.operational_failures == 0


def test_safe_first_source_match_stops_fallback_for_that_track(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FallbackSearch({"qobuz": "match", "tidal": "match"})

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            search,
            ("qobuz", "tidal"),
            lambda source: FakeDownloader(source),
            tmp_path / "library",
            tmp_path / "playlists",
        )

    assert [item.source for item in report.resolutions] == ["qobuz"]
    assert search.calls == [("spotify-1", "qobuz")]


def test_resolution_limit_freezes_same_distinct_track_set_across_sources(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FallbackSearch({"qobuz": "none", "tidal": "match"})

    with connect(database) as connection:
        report = preview_update_workflow(
            connection,
            _snapshot(("spotify-a", "spotify-b", "spotify-c")),
            search,
            ("qobuz", "tidal"),
            lambda source: FakeDownloader(source),
            resolution_limit=1,
        )

    searched_ids = {spotify_id for spotify_id, _ in search.calls}
    assert len(searched_ids) == 1
    selected_id = next(iter(searched_ids))
    assert search.calls == [
        (selected_id, "qobuz"),
        (selected_id, "tidal"),
    ]
    assert sum(item.resolved for item in report.resolutions) == 1


def test_cli_custom_source_order_is_used_for_fallback(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setattr("synctify.cli_entry._fetch_update_snapshot", lambda settings: _snapshot())
    monkeypatch.setattr(
        "synctify.cli_entry.StreamripCatalogSearch.require_noninteractive_source_ready",
        lambda self, source: None,
    )
    calls: list[str] = []

    def fake_search(self, track, source: str, limit=None):
        calls.append(source)
        if source == "tidal":
            return []
        return [Candidate(source, f"{source}-match", track.title, track.artist)]

    monkeypatch.setattr("synctify.cli_entry.StreamripCatalogSearch.search", fake_search)

    result = CliRunner().invoke(
        app,
        ["update", "--dry-run", "--sources", "tidal,qobuz"],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["tidal", "qobuz"]
    assert "tidal -> qobuz" in result.output
