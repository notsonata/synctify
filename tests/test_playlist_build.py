from __future__ import annotations

import os
from pathlib import Path

from synctify.db import connect, initialize
from synctify.playlists import build_playlists, playlist_marker


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


def _complete_seed(tmp_path: Path) -> tuple[Path, Path, Path]:
    first = tmp_path / "library" / "First.flac"
    second = tmp_path / "library" / "Second.flac"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    database = tmp_path / "state.sqlite3"
    seed_playlist(database, first, second)
    return database, first, second


def test_build_playlist_preserves_spotify_order_and_records_ownership(tmp_path: Path) -> None:
    database, _, _ = _complete_seed(tmp_path)

    with connect(database) as connection:
        report = build_playlists(connection, tmp_path / "playlists")
        ownership = connection.execute(
            "SELECT playlist_id, filename FROM generated_playlists"
        ).fetchone()

    assert report.written == 1
    output = report.results[0].output
    assert output is not None
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == playlist_marker("playlist-1")
    assert lines[2].endswith("Second.flac")
    assert lines[3].endswith("First.flac")
    assert ownership["playlist_id"] == "playlist-1"
    assert ownership["filename"] == output.name


def test_incomplete_playlist_is_not_written_by_default(tmp_path: Path) -> None:
    first = tmp_path / "library" / "First.flac"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    database = tmp_path / "state.sqlite3"
    seed_playlist(database, first, None)

    with connect(database) as connection:
        report = build_playlists(connection, tmp_path / "playlists")
        owned = connection.execute("SELECT COUNT(*) FROM generated_playlists").fetchone()[0]

    assert report.written == 0
    assert report.incomplete == 1
    assert report.results[0].missing_tracks == ("Artist - Second",)
    assert owned == 0


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
    assert playlist_marker("playlist-1") in content
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


def test_strict_build_removes_previously_owned_output_when_playlist_becomes_incomplete(
    tmp_path: Path,
) -> None:
    database, _, second = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"

    with connect(database) as connection:
        first_report = build_playlists(connection, playlist_dir)
    output = first_report.results[0].output
    assert output is not None and output.is_file()

    second.unlink()
    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)
        owned = connection.execute("SELECT COUNT(*) FROM generated_playlists").fetchone()[0]

    assert report.written == 0
    assert report.incomplete == 1
    assert report.removed_outputs == (output.resolve(),)
    assert not output.exists()
    assert owned == 0


def test_removed_playlist_removes_only_its_owned_generated_file(tmp_path: Path) -> None:
    database, _, _ = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"
    manual = playlist_dir / "Manual.m3u8"
    playlist_dir.mkdir(parents=True)
    manual.write_text("#EXTM3U\n../manual.flac\n", encoding="utf-8")

    with connect(database) as connection:
        first_report = build_playlists(connection, playlist_dir)
        connection.execute("DELETE FROM playlists WHERE spotify_id = 'playlist-1'")
    output = first_report.results[0].output
    assert output is not None

    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)
        owned = connection.execute("SELECT COUNT(*) FROM generated_playlists").fetchone()[0]

    assert report.removed_outputs == (output.resolve(),)
    assert not output.exists()
    assert manual.is_file()
    assert owned == 0


def test_playlist_rename_removes_old_owned_filename(tmp_path: Path) -> None:
    database, _, _ = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"

    with connect(database) as connection:
        first_report = build_playlists(connection, playlist_dir)
        connection.execute(
            "UPDATE playlists SET name = 'Road Trip' WHERE spotify_id = 'playlist-1'"
        )
    old_output = first_report.results[0].output
    assert old_output is not None

    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)

    new_output = report.results[0].output
    assert new_output is not None
    assert new_output.name == "Road Trip.m3u8"
    assert new_output.is_file()
    assert not old_output.exists()
    assert old_output.resolve() in report.removed_outputs


def test_unmarked_user_file_at_desired_name_is_not_overwritten(tmp_path: Path) -> None:
    database, _, _ = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir(parents=True)
    user_file = playlist_dir / "Driving _ Night.m3u8"
    user_content = "#EXTM3U\n../My Manual Song.flac\n"
    user_file.write_text(user_content, encoding="utf-8")

    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)

    output = report.results[0].output
    assert output is not None
    assert output != user_file
    assert "[synctify-" in output.name
    assert user_file.read_text(encoding="utf-8") == user_content


def test_owned_file_without_marker_is_protected_instead_of_deleted(tmp_path: Path) -> None:
    database, _, second = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"

    with connect(database) as connection:
        first_report = build_playlists(connection, playlist_dir)
    output = first_report.results[0].output
    assert output is not None
    replacement = "#EXTM3U\n../User Replacement.flac\n"
    output.write_text(replacement, encoding="utf-8")
    second.unlink()

    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)
        owned = connection.execute("SELECT COUNT(*) FROM generated_playlists").fetchone()[0]

    assert report.removed_outputs == ()
    assert report.protected_outputs == (output.resolve(),)
    assert output.read_text(encoding="utf-8") == replacement
    assert owned == 0


def test_exact_legacy_synctify_output_is_adopted_and_marked(tmp_path: Path) -> None:
    database, first, second = _complete_seed(tmp_path)
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir(parents=True)
    legacy = playlist_dir / "Driving _ Night.m3u8"
    legacy.write_text(
        "#EXTM3U\n"
        + Path(os.path.relpath(second, start=playlist_dir)).as_posix()
        + "\n"
        + Path(os.path.relpath(first, start=playlist_dir)).as_posix()
        + "\n",
        encoding="utf-8",
    )

    with connect(database) as connection:
        report = build_playlists(connection, playlist_dir)
        owned = connection.execute(
            "SELECT filename FROM generated_playlists WHERE playlist_id = 'playlist-1'"
        ).fetchone()

    output = report.results[0].output
    assert output == legacy
    assert playlist_marker("playlist-1") in legacy.read_text(encoding="utf-8")
    assert owned["filename"] == legacy.name
