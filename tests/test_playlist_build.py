from __future__ import annotations

from pathlib import Path

from synctify.db import connect, initialize
from synctify.playlists import build_playlists


def seed_playlist(database: Path, first_path: Path, second_path: Path | None) -> None:
    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
            ("playlist-1", "Driving / Night"),
        )
        connection.executemany(
            """
            INSERT INTO tracks(spotify_id, title, artist, album, local_path, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("track-1", "First", "Artist", "Album", str(first_path), "local"),
                ("track-2", "Second", "Artist", "Album", str(second_path) if second_path else None, "local" if second_path else "unresolved"),
            ],
        )
        connection.executemany(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, ?)",
            [
                ("playlist-1", "track-2", 0),
                ("playlist-1", "track-1", 1),
            ],
        )


def test_build_playlist_preserves_spotify_order(tmp_path: Path) -> None:
    first = tmp_path / "library" / "First.flac"
    second = tmp_path / "library" / "Second.flac"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    database = tmp_path / "state.sqlite3"
    seed_playlist(database, first, second)

    with connect(database) as connection:
        report = build_playlists(connection, tmp_path / "playlists")

    assert report.written == 1
    output = report.results[0].output
    assert output is not None
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1].endswith("Second.flac")
    assert lines[2].endswith("First.flac")


def test_incomplete_playlist_is_not_written_by_default(tmp_path: Path) -> None:
    first = tmp_path / "library" / "First.flac"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    database = tmp_path / "state.sqlite3"
    seed_playlist(database, first, None)

    with connect(database) as connection:
        report = build_playlists(connection, tmp_path / "playlists")

    assert report.written == 0
    assert report.incomplete == 1
    assert report.results[0].missing_tracks == ("Artist - Second",)


def test_allow_partial_writes_only_available_tracks(tmp_path: Path) -> None:
    first = tmp_path / "library" / "First.flac"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    database = tmp_path / "state.sqlite3"
    seed_playlist(database, first, None)

    with connect(database) as connection:
        report = build_playlists(connection, tmp_path / "playlists", allow_partial=True)

    output = report.results[0].output
    assert output is not None
    content = output.read_text(encoding="utf-8")
    assert "First.flac" in content
    assert "Second" not in content
    assert report.results[0].written_tracks == 1


def test_duplicate_playlist_names_get_distinct_files(tmp_path: Path) -> None:
    track = tmp_path / "library" / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"track")
    database = tmp_path / "state.sqlite3"
    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, local_path, status) VALUES (?, ?, ?, ?, 'local')",
            ("track-1", "Track", "Artist", str(track)),
        )
        connection.executemany(
            "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
            [("playlist-abcdefgh", "Same"), ("playlist-ijklmnop", "Same")],
        )
        connection.executemany(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, 0)",
            [("playlist-abcdefgh", "track-1"), ("playlist-ijklmnop", "track-1")],
        )
        report = build_playlists(connection, tmp_path / "playlists")

    outputs = {result.output.name for result in report.results if result.output is not None}
    assert len(outputs) == 2
