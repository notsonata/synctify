from __future__ import annotations

import hashlib
from pathlib import Path

from synctify.acquisition import acquire_tasks, pending_acquisitions
from synctify.db import connect, initialize
from synctify.models import Track
from synctify.providers.base import AcquiredTrack
from synctify.resolution import Candidate, set_manual_override


class FakeQobuzProvider:
    name = "qobuz"

    def __init__(self, *, fail_ids: set[str] | None = None) -> None:
        self.fail_ids = fail_ids or set()

    def search(self, track: Track) -> tuple[Candidate, ...]:
        return ()

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        if candidate.provider_track_id in self.fail_ids:
            raise RuntimeError("simulated download failure")
        path = destination / candidate.artist / candidate.album / f"{candidate.title}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"FLAC:{candidate.provider_track_id}".encode())
        return AcquiredTrack(
            provider=self.name,
            provider_track_id=candidate.provider_track_id,
            path=path,
        )


def _insert_track(connection, spotify_id: str, title: str, *, local_path: str | None = None) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, isrc, title, artist, album, duration_ms, local_path)
        VALUES (?, ?, ?, 'Artist', 'Album', 200000, ?)
        """,
        (spotify_id, f"ISRC{spotify_id}", title, local_path),
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


def test_acquire_tasks_records_local_path_hash_and_qobuz_id(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    library = tmp_path / "library"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", "Track")
        set_manual_override(connection, "spotify-1", "qobuz", "123")
        tasks = pending_acquisitions(connection, provider="qobuz")

        report = acquire_tasks(connection, FakeQobuzProvider(), tasks, library)
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
            FakeQobuzProvider(fail_ids={"bad"}),
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
