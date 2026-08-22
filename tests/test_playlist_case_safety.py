from __future__ import annotations

from pathlib import Path

from synctify.db import connect, initialize
from synctify.playlists import build_playlists, playlist_marker


def _seed_track(connection, path: Path) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, title, artist, local_path, status)
        VALUES ('track-1', 'Track', 'Artist', ?, 'local')
        """,
        (str(path),),
    )


def _seed_playlist(connection, spotify_id: str, name: str) -> None:
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
        (spotify_id, name),
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, 'track-1', 0)",
        (spotify_id,),
    )


def test_case_only_rename_reuses_owned_filename_instead_of_deleting_it(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    playlist_dir = tmp_path / "playlists"
    track = tmp_path / "library" / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"track")
    initialize(database)

    with connect(database) as connection:
        _seed_track(connection, track)
        _seed_playlist(connection, "playlist-1", "Mix")
        first = build_playlists(connection, playlist_dir)
        connection.execute(
            "UPDATE playlists SET name = 'mix' WHERE spotify_id = 'playlist-1'"
        )

    original = first.results[0].output
    assert original is not None
    assert original.name == "Mix.m3u8"

    with connect(database) as connection:
        second = build_playlists(connection, playlist_dir)
        ownership = connection.execute(
            "SELECT filename FROM generated_playlists WHERE playlist_id = 'playlist-1'"
        ).fetchone()

    output = second.results[0].output
    assert output == original
    assert output is not None and output.is_file()
    assert playlist_marker("playlist-1") in output.read_text(encoding="utf-8")
    assert second.removed_outputs == ()
    assert ownership["filename"] == "Mix.m3u8"


def test_case_colliding_playlist_names_get_portably_distinct_outputs(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    playlist_dir = tmp_path / "playlists"
    track = tmp_path / "library" / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"track")
    initialize(database)

    with connect(database) as connection:
        _seed_track(connection, track)
        _seed_playlist(connection, "playlist-abcdefgh", "Rock")
        _seed_playlist(connection, "playlist-ijklmnop", "rock")
        report = build_playlists(connection, playlist_dir)

    outputs = [result.output for result in report.results]
    assert all(output is not None for output in outputs)
    names = [output.name for output in outputs if output is not None]
    assert len(names) == 2
    assert len({name.casefold() for name in names}) == 2
    assert all("[" in name for name in names)


def test_user_file_with_case_equivalent_name_is_never_overwritten(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    playlist_dir = tmp_path / "playlists"
    track = tmp_path / "library" / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"track")
    playlist_dir.mkdir(parents=True)
    user_file = playlist_dir / "MIX.m3u8"
    user_content = "#EXTM3U\n../Manual.flac\n"
    user_file.write_text(user_content, encoding="utf-8")
    initialize(database)

    with connect(database) as connection:
        _seed_track(connection, track)
        _seed_playlist(connection, "playlist-1", "Mix")
        report = build_playlists(connection, playlist_dir)

    output = report.results[0].output
    assert output is not None
    assert output.name.casefold() != user_file.name.casefold()
    assert "[synctify-" in output.name
    assert user_file.read_text(encoding="utf-8") == user_content
