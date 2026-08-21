from __future__ import annotations

import hashlib
from pathlib import Path

from synctify.acquisition import acquire_tasks, pending_acquisitions
from synctify.db import connect, initialize
from synctify.providers.base import AcquiredTrack
from synctify.resolution import Candidate, set_manual_override


class FakeDownloader:
    name = "fake-downloader"
    supported_sources = frozenset({"qobuz", "tidal"})

    def __init__(self, *, fail_ids: set[str] | None = None) -> None:
        self.fail_ids = fail_ids or set()

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        if candidate.provider_track_id in self.fail_ids:
            raise RuntimeError("simulated download failure")
        path = destination / candidate.artist / candidate.album / f"{candidate.title}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"FLAC:{candidate.provider_track_id}".encode())
        return AcquiredTrack(
            provider=candidate.provider,
            provider_track_id=candidate.provider_track_id,
            path=path,
        )


def _insert_track(
    connection,
    spotify_id: str,
    title: str,
    *,
    local_path: str | None = None,
    referenced: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, isrc, title, artist, album, duration_ms, local_path)
        VALUES (?, ?, ?, 'Artist', 'Album', 200000, ?)
        """,
        (spotify_id, f"ISRC{spotify_id}", title, local_path),
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


def test_pending_acquisitions_only_returns_resolved_missing_tracks(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    existing = tmp_path / "existing.flac"
    existing.write_bytes(b"existing")

    with connect(database) as connection:
        _insert_track(connection, "missing", "Missing")
        _insert_track(connection, "local", "Local", local_path=str(existing))
        _insert_track(connection, "unresolved", "Unresolved")
        set_manual_override(connection, "missing", "qobuz", "q1")
        set_manual_override(connection, "local", "qobuz", "q2")

        tasks = pending_acquisitions(connection, provider="qobuz")

    assert [task.spotify_id for task in tasks] == ["missing"]
    assert tasks[0].provider_track_id == "q1"


def test_pending_acquisitions_excludes_unreferenced_resolved_track(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "wanted", "Wanted")
        _insert_track(connection, "orphan", "Orphan", referenced=False)
        set_manual_override(connection, "wanted", "qobuz", "q1")
        set_manual_override(connection, "orphan", "qobuz", "q2")

        tasks = pending_acquisitions(connection, provider="qobuz")

    assert [task.spotify_id for task in tasks] == ["wanted"]


def test_pending_acquisitions_filters_by_source_service(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "qobuz-track", "Qobuz")
        _insert_track(connection, "tidal-track", "Tidal")
        set_manual_override(connection, "qobuz-track", "qobuz", "q1")
        set_manual_override(connection, "tidal-track", "tidal", "t1")

        tidal_tasks = pending_acquisitions(connection, provider="tidal")

    assert [task.spotify_id for task in tidal_tasks] == ["tidal-track"]
    assert tidal_tasks[0].provider == "tidal"


def test_acquire_tasks_records_local_path_hash_and_qobuz_id(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    library = tmp_path / "library"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", "Track")
        set_manual_override(connection, "spotify-1", "qobuz", "123")
        tasks = pending_acquisitions(connection, provider="qobuz")

        report = acquire_tasks(connection, FakeDownloader(), tasks, library)
        row = connection.execute(
            "SELECT qobuz_id, local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    expected_bytes = b"FLAC:123"
    assert report.succeeded == 1
    assert report.failed == 0
    assert row["qobuz_id"] == "123"
    assert Path(row["local_path"]).is_file()
    assert row["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert row["status"] == "local"


def test_acquire_tasks_accepts_non_qobuz_source_when_downloader_supports_it(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    library = tmp_path / "library"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-tidal", "Track")
        set_manual_override(connection, "spotify-tidal", "tidal", "987")
        tasks = pending_acquisitions(connection, provider="tidal")

        report = acquire_tasks(connection, FakeDownloader(), tasks, library)
        row = connection.execute(
            "SELECT qobuz_id, local_path, status FROM tracks WHERE spotify_id = 'spotify-tidal'"
        ).fetchone()

    assert report.succeeded == 1
    assert row["qobuz_id"] is None
    assert Path(row["local_path"]).is_file()
    assert row["status"] == "local"


def test_acquire_tasks_rejects_source_the_downloader_does_not_support(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-deezer", "Track")
        set_manual_override(connection, "spotify-deezer", "deezer", "321")
        tasks = pending_acquisitions(connection, provider="deezer")

        report = acquire_tasks(connection, FakeDownloader(), tasks, tmp_path / "library")

    assert report.succeeded == 0
    assert report.failed == 1
    assert "does not support source 'deezer'" in report.failures[0].message


def test_acquire_tasks_continues_after_provider_failure(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-a", "A")
        _insert_track(connection, "spotify-b", "B")
        set_manual_override(connection, "spotify-a", "qobuz", "bad")
        set_manual_override(connection, "spotify-b", "qobuz", "good")
        tasks = pending_acquisitions(connection, provider="qobuz")

        report = acquire_tasks(
            connection,
            FakeDownloader(fail_ids={"bad"}),
            tasks,
            tmp_path / "library",
        )
        rows = {
            row["spotify_id"]: row["local_path"]
            for row in connection.execute("SELECT spotify_id, local_path FROM tracks")
        }

    assert report.succeeded == 1
    assert report.failed == 1
    assert report.failures[0].spotify_id == "spotify-a"
    assert rows["spotify-a"] is None
    assert rows["spotify-b"] is not None
