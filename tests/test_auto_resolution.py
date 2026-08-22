from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from synctify.auto_resolution import auto_resolve_tracks, pending_resolution_tracks
from synctify.cli_entry import app
from synctify.db import connect, initialize
from synctify.models import Track
from synctify.resolution import Candidate, set_manual_override


class FakeSearch:
    name = "fake-search"
    supported_sources = frozenset({"qobuz", "tidal"})

    def __init__(self, candidates: list[Candidate] | None = None, *, error: str | None = None) -> None:
        self.candidates = candidates or []
        self.error = error
        self.calls: list[tuple[str, str, int | None]] = []

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def search(self, track: Track, source: str, *, limit: int | None = None) -> list[Candidate]:
        self.calls.append((track.spotify_id, source, limit))
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.candidates


def _insert_track(
    connection,
    spotify_id: str,
    *,
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    referenced: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, title, artist, album, isrc, duration_ms)
        VALUES (?, ?, ?, 'OK Computer', 'GBAYE9701376', 386000)
        """,
        (spotify_id, title, artist),
    )
    if referenced:
        connection.execute(
            "INSERT OR IGNORE INTO playlists(spotify_id, name) VALUES ('desired', 'Desired')"
        )
        position = connection.execute(
            "SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = 'desired'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('desired', ?, ?)",
            (spotify_id, position),
        )


def test_auto_resolution_persists_safe_metadata_match(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(
        [Candidate("qobuz", "q-123", "Paranoid Android", "Radiohead")]
    )

    with connect(database) as connection:
        _insert_track(connection, "spotify-1")
        report = auto_resolve_tracks(connection, search, "qobuz")
        row = connection.execute(
            """
            SELECT provider, provider_track_id, match_method, confidence, is_manual
            FROM track_resolutions WHERE spotify_id = 'spotify-1'
            """
        ).fetchone()

    assert report.resolved == 1
    assert report.ambiguous == 0
    assert report.failed == 0
    assert row["provider"] == "qobuz"
    assert row["provider_track_id"] == "q-123"
    assert row["match_method"] == "metadata"
    assert row["confidence"] == 1.0
    assert row["is_manual"] == 0


def test_unreferenced_track_is_not_searched(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(
        [Candidate("qobuz", "q-123", "Paranoid Android", "Radiohead")]
    )

    with connect(database) as connection:
        _insert_track(connection, "spotify-orphan", referenced=False)
        report = auto_resolve_tracks(connection, search, "qobuz")

    assert report.attempts == ()
    assert search.calls == []


def test_ambiguous_search_results_are_not_persisted(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(
        [
            Candidate("qobuz", "q-1", "Paranoid Android", "Radiohead"),
            Candidate("qobuz", "q-2", "Paranoid Android", "Radiohead"),
        ]
    )

    with connect(database) as connection:
        _insert_track(connection, "spotify-1")
        report = auto_resolve_tracks(connection, search, "qobuz")
        stored = connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0]

    assert report.resolved == 0
    assert report.ambiguous == 1
    assert stored == 0


def test_dry_run_never_persists_resolution(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(
        [Candidate("qobuz", "q-123", "Paranoid Android", "Radiohead")]
    )

    with connect(database) as connection:
        _insert_track(connection, "spotify-1")
        report = auto_resolve_tracks(connection, search, "qobuz", dry_run=True)
        stored = connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0]

    assert report.dry_run is True
    assert report.resolved == 1
    assert stored == 0


def test_existing_manual_resolution_is_never_searched_or_overwritten(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(
        [Candidate("qobuz", "automatic", "Paranoid Android", "Radiohead")]
    )

    with connect(database) as connection:
        _insert_track(connection, "spotify-1")
        set_manual_override(connection, "spotify-1", "qobuz", "manual")
        report = auto_resolve_tracks(connection, search, "qobuz")
        row = connection.execute(
            "SELECT provider_track_id, is_manual FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert report.attempts == ()
    assert search.calls == []
    assert row["provider_track_id"] == "manual"
    assert row["is_manual"] == 1


def test_limit_applies_before_search(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch([])

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", title="A")
        _insert_track(connection, "spotify-2", title="B")
        _insert_track(connection, "spotify-3", title="C")
        tracks = pending_resolution_tracks(connection, limit=2)
        report = auto_resolve_tracks(connection, search, "qobuz", limit=2, search_results=4)

    assert len(tracks) == 2
    assert len(report.attempts) == 2
    assert len(search.calls) == 2
    assert all(call[2] == 4 for call in search.calls)


def test_auto_resolution_reports_each_track_before_search(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch([])
    progress: list[tuple[str, int, int, str]] = []

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", title="A")
        _insert_track(connection, "spotify-2", title="B")
        auto_resolve_tracks(
            connection,
            search,
            "qobuz",
            progress=lambda source, current, total, track: progress.append(
                (source, current, total, track.spotify_id)
            ),
        )

    assert progress == [
        ("qobuz", 1, 2, "spotify-1"),
        ("qobuz", 2, 2, "spotify-2"),
    ]


def test_search_failure_is_reported_without_stopping_remaining_tracks(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    search = FakeSearch(error="catalog unavailable")

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", title="A")
        _insert_track(connection, "spotify-2", title="B")
        report = auto_resolve_tracks(connection, search, "qobuz")

    assert report.failed == 2
    assert len(report.attempts) == 2
    assert all(attempt.error == "catalog unavailable" for attempt in report.attempts)


def test_cli_resolve_auto_defaults_to_persisting_safe_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path / "home"))
    database = tmp_path / "home" / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        _insert_track(connection, "spotify-1")

    monkeypatch.setattr(
        "synctify.cli_entry.StreamripCatalogSearch.require_available",
        lambda self: None,
    )
    monkeypatch.setattr(
        "synctify.cli_entry.StreamripCatalogSearch.search",
        lambda self, track, source, limit=None: [
            Candidate(source, "q-123", track.title, track.artist)
        ],
    )

    result = CliRunner().invoke(app, ["resolve", "auto", "--source", "qobuz"])

    assert result.exit_code == 0, result.output
    assert "Resolved: 1" in result.output
    with connect(database) as connection:
        row = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()
    assert row["provider"] == "qobuz"
    assert row["provider_track_id"] == "q-123"
