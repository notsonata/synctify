from __future__ import annotations

from pathlib import Path
import sqlite3

from synctify.db import connect, initialize
from synctify.models import Track
from synctify.providers import AcquiredTrack, AcquisitionProvider
from synctify.resolution import (
    Candidate,
    ManualOverride,
    MatchMethod,
    ResolutionStatus,
    clear_resolution,
    get_manual_override,
    metadata_score,
    resolve_track,
    set_manual_override,
)


def source_track(**overrides: object) -> Track:
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


def test_exact_isrc_beats_better_fuzzy_metadata() -> None:
    track = source_track()
    exact = Candidate(
        "qobuz",
        "q-exact",
        "Paranoid Android (Remastered)",
        "Radiohead",
        "OK Computer",
        "GB-AYE-97-01376",
        386_500,
    )
    fuzzy = Candidate(
        "qobuz",
        "q-fuzzy",
        "Paranoid Android",
        "Radiohead",
        "OK Computer",
        "DIFFERENTISRC",
        386_000,
    )

    result = resolve_track(track, [fuzzy, exact])

    assert result.status is ResolutionStatus.RESOLVED
    assert result.method is MatchMethod.ISRC
    assert result.candidate == exact
    assert result.confidence == 1.0


def test_duration_breaks_an_otherwise_identical_metadata_tie() -> None:
    track = source_track(isrc=None, duration_ms=180_000)
    correct = Candidate("qobuz", "correct", track.title, track.artist, track.album, None, 181_000)
    wrong_length = Candidate("qobuz", "wrong", track.title, track.artist, track.album, None, 245_000)

    assert metadata_score(track, correct) > metadata_score(track, wrong_length)
    result = resolve_track(track, [wrong_length, correct])

    assert result.status is ResolutionStatus.RESOLVED
    assert result.method is MatchMethod.METADATA
    assert result.candidate == correct


def test_ambiguous_candidates_are_not_auto_resolved() -> None:
    track = source_track(isrc=None)
    candidates = [
        Candidate("qobuz", "a", track.title, track.artist, track.album, None, track.duration_ms),
        Candidate("qobuz", "b", track.title, track.artist, track.album, None, track.duration_ms),
    ]

    result = resolve_track(track, candidates)

    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.candidate is None


def test_low_confidence_candidate_stays_unresolved() -> None:
    track = source_track(isrc=None)
    candidate = Candidate("qobuz", "wrong", "Completely Different", "Another Artist", "Other Album", None, 90_000)

    result = resolve_track(track, [candidate])

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.candidate is None


def test_manual_override_wins_over_automatic_candidates() -> None:
    track = source_track()
    automatic = Candidate("qobuz", "auto", track.title, track.artist, track.album, track.isrc, track.duration_ms)

    result = resolve_track(
        track,
        [automatic],
        override=ManualOverride("qobuz", "chosen-by-user"),
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.method is MatchMethod.MANUAL
    assert result.candidate is not None
    assert result.candidate.provider_track_id == "chosen-by-user"


def test_manual_resolution_persists_and_can_be_cleared(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, album, isrc, duration_ms) VALUES (?, ?, ?, ?, ?, ?)",
            ("spotify-1", "Song", "Artist", "Album", "USABC1234567", 180_000),
        )
        set_manual_override(connection, "spotify-1", "qobuz", "q-123")
        override = get_manual_override(connection, "spotify-1")
        stored = connection.execute(
            "SELECT match_method, confidence, is_manual FROM track_resolutions WHERE spotify_id = ?",
            ("spotify-1",),
        ).fetchone()

        assert override == ManualOverride("qobuz", "q-123")
        assert stored["match_method"] == "manual"
        assert stored["confidence"] == 1.0
        assert stored["is_manual"] == 1
        assert clear_resolution(connection, "spotify-1") is True
        assert get_manual_override(connection, "spotify-1") is None


def test_v2_database_migrates_to_resolution_schema(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tracks (
                spotify_id TEXT PRIMARY KEY,
                isrc TEXT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT,
                duration_ms INTEGER,
                qobuz_id TEXT,
                local_path TEXT,
                sha256 TEXT,
                status TEXT NOT NULL DEFAULT 'unresolved'
            );
            CREATE TABLE playlists (
                spotify_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                snapshot_id TEXT,
                last_checked_at TEXT,
                source_kind TEXT NOT NULL DEFAULT 'playlist',
                owner_id TEXT,
                collaborative INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO metadata(key, value) VALUES ('schema_version', '2');
            """
        )

    initialize(database)

    with connect(database) as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()[0]

    assert "track_resolutions" in tables
    assert version == "3"


def test_acquisition_provider_protocol_is_runtime_checkable(tmp_path: Path) -> None:
    class FakeProvider:
        name = "fake-downloader"
        supported_sources = frozenset({"fake"})

        def supports(self, source: str) -> bool:
            return source in self.supported_sources

        def search(self, track: Track) -> list[Candidate]:
            return [Candidate("fake", "1", track.title, track.artist)]

        def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
            return AcquiredTrack(candidate.provider, candidate.provider_track_id, destination / "track.flac")

    provider = FakeProvider()
    assert isinstance(provider, AcquisitionProvider)
    assert provider.supports("fake") is True
    assert provider.acquire(provider.search(source_track())[0], tmp_path).path.name == "track.flac"
