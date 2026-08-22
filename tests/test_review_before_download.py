from __future__ import annotations

from pathlib import Path

from synctify.auto_resolution import pending_resolution_tracks
from synctify.db import connect, initialize
from synctify.local_reconcile import reconcile_confirmed_local_tracks


def _insert_confirmed_track(
    connection,
    spotify_id: str,
    *,
    local_path: Path | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(
            spotify_id, title, artist, album, isrc, duration_ms, local_path, status
        ) VALUES (?, 'Song', 'Artist', 'Album', 'USAAA2600001', 180000, ?, ?)
        """,
        (
            spotify_id,
            None if local_path is None else str(local_path),
            "unresolved" if local_path is None else "local",
        ),
    )
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES ('playlist-1', 'Playlist')"
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', ?, 0)",
        (spotify_id,),
    )


def test_valid_recorded_local_flac_never_enters_provider_resolution(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    library.mkdir()
    flac = library / "Artist - Song.flac"
    flac.write_bytes(b"already-local")

    initialize(database)
    with connect(database) as connection:
        _insert_confirmed_track(connection, "spotify-1", local_path=flac)

        report = reconcile_confirmed_local_tracks(connection, library)
        pending = pending_resolution_tracks(connection)

    assert report.reused == 1
    assert report.matched == 0
    assert pending == ()
    assert flac.exists()


def test_unrecorded_local_flac_is_attached_before_provider_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    library.mkdir()
    flac = library / "Existing.flac"
    flac.write_bytes(b"existing")

    class FakeIndex:
        def __init__(self, root: Path) -> None:
            assert root == library

        def find(self, candidate):
            assert candidate.title == "Song"
            assert candidate.artist == "Artist"
            assert candidate.isrc == "USAAA2600001"
            return flac

    monkeypatch.setattr("synctify.local_reconcile.FLACReconciliationIndex", FakeIndex)
    monkeypatch.setattr("synctify.local_reconcile.file_sha256", lambda _path: "abc123")

    initialize(database)
    with connect(database) as connection:
        _insert_confirmed_track(connection, "spotify-1")

        report = reconcile_confirmed_local_tracks(connection, library)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()
        pending = pending_resolution_tracks(connection)

    assert report.reused == 0
    assert report.matched == 1
    assert row["local_path"] == str(flac)
    assert row["sha256"] == "abc123"
    assert row["status"] == "local"
    assert pending == ()


def test_schema_v7_requires_review_without_deleting_local_flacs(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    library.mkdir()
    flac = library / "Legacy.flac"
    flac.write_bytes(b"legacy")

    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, local_path, status) VALUES ('track-1', 'Song', 'Artist', ?, 'local')",
            (str(flac),),
        )
        connection.execute(
            "INSERT INTO playlists(spotify_id, name, source_kind, collaborative) VALUES ('playlist-1', 'Legacy', 'playlist', 0)"
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', 'track-1', 0)"
        )
        connection.execute(
            """
            INSERT INTO spotify_playlist_catalog(
                spotify_id, name, source_kind, collaborative, tracked, available, fetched_at
            ) VALUES ('playlist-1', 'Legacy', 'playlist', 0, 1, 1, '2026-08-23T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO spotify_playlist_items(
                playlist_id, item_key, track_id, position, title, artist, state, present
            ) VALUES ('playlist-1', 'track-1:key', 'track-1', 0, 'Song', 'Artist', 'included', 1)
            """
        )
        connection.execute(
            "UPDATE metadata SET value = '6' WHERE key = 'schema_version'"
        )

    initialize(database)

    with connect(database) as connection:
        schema = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()["value"]
        catalog = connection.execute(
            "SELECT tracked FROM spotify_playlist_catalog WHERE spotify_id = 'playlist-1'"
        ).fetchone()
        item = connection.execute(
            "SELECT state FROM spotify_playlist_items WHERE playlist_id = 'playlist-1'"
        ).fetchone()
        active = connection.execute(
            "SELECT COUNT(*) FROM playlists WHERE spotify_id = 'playlist-1'"
        ).fetchone()[0]
        track = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'track-1'"
        ).fetchone()

    assert schema == "7"
    assert catalog["tracked"] == 0
    assert item["state"] == "pending_add"
    assert active == 0
    assert track["local_path"] == str(flac)
    assert flac.exists()
