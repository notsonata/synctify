from __future__ import annotations

import hashlib
from pathlib import Path

from synctify.acquisition import AcquisitionTask, acquire_tasks
from synctify.db import connect, initialize
from synctify.providers.base import AcquiredTrack
from synctify.resolution import Candidate


class ReconciledProvider:
    name = "fake"
    supported_sources = frozenset({"qobuz"})

    def __init__(self, path: Path) -> None:
        self.path = path

    def supports(self, source: str) -> bool:
        return source == "qobuz"

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        return AcquiredTrack(
            provider=candidate.provider,
            provider_track_id=candidate.provider_track_id,
            path=self.path,
            reconciled=True,
        )


def test_acquire_tasks_persists_reconciled_file_and_reports_it(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    existing = library / "Artist" / "Album" / "Track.flac"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing-flac")
    initialize(database)

    task = AcquisitionTask(
        spotify_id="spotify-1",
        provider="qobuz",
        provider_track_id="qobuz-1",
        title="Track",
        artist="Artist",
        album="Album",
        isrc="USABC1234567",
        duration_ms=200_000,
    )

    with connect(database) as connection:
        connection.execute(
            """
            INSERT INTO tracks(spotify_id, title, artist, album, isrc, duration_ms)
            VALUES ('spotify-1', 'Track', 'Artist', 'Album', 'USABC1234567', 200000)
            """
        )
        report = acquire_tasks(
            connection,
            ReconciledProvider(existing),
            (task,),
            library,
        )
        row = connection.execute(
            "SELECT qobuz_id, local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert report.succeeded == 1
    assert report.downloaded == 0
    assert report.reconciled == 1
    assert report.failed == 0
    assert report.completed[0].reconciled is True
    assert row["qobuz_id"] == "qobuz-1"
    assert Path(row["local_path"]) == existing.resolve()
    assert row["sha256"] == hashlib.sha256(b"existing-flac").hexdigest()
    assert row["status"] == "local"
