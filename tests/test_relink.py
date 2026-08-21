from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from synctify.acquisition import file_sha256
from synctify.db import connect, initialize
from synctify.relink import relink_library
from synctify.relink_cli import app


def _metadata_block(block_type: int, payload: bytes, *, last: bool) -> bytes:
    first = block_type | (0x80 if last else 0)
    return bytes([first]) + len(payload).to_bytes(3, "big") + payload


def _streaminfo(duration_ms: int, sample_rate: int = 44_100) -> bytes:
    total_samples = round(duration_ms * sample_rate / 1000)
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    return b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16


def _vorbis_comments(tags: dict[str, list[str]]) -> bytes:
    vendor = b"synctify-relink-test"
    comments = [
        f"{key.upper()}={value}".encode("utf-8")
        for key, values in tags.items()
        for value in values
    ]
    payload = len(vendor).to_bytes(4, "little") + vendor
    payload += len(comments).to_bytes(4, "little")
    for comment in comments:
        payload += len(comment).to_bytes(4, "little") + comment
    return payload


def _write_flac(
    path: Path,
    *,
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    album: str = "OK Computer",
    isrc: str = "GBAYE9701376",
    duration_ms: int = 386_000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = {
        "title": [title],
        "artist": [artist],
        "album": [album],
        "isrc": [isrc],
    }
    data = b"fLaC"
    data += _metadata_block(0, _streaminfo(duration_ms), last=False)
    data += _metadata_block(4, _vorbis_comments(tags), last=True)
    path.write_bytes(data)


def _desired_track(
    connection,
    spotify_id: str = "spotify-1",
    *,
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    album: str = "OK Computer",
    isrc: str = "GBAYE9701376",
    duration_ms: int = 386_000,
    local_path: str | None = None,
    sha256: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(
            spotify_id, isrc, title, artist, album, duration_ms, local_path, sha256, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spotify_id,
            isrc,
            title,
            artist,
            album,
            duration_ms,
            local_path,
            sha256,
            "local" if local_path else "unresolved",
        ),
    )
    playlist_id = f"playlist-{spotify_id}"
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
        (playlist_id, f"Playlist {spotify_id}"),
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, 0)",
        (playlist_id, spotify_id),
    )


def test_preview_external_library_plans_copy_without_mutation(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    track = source / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    _write_flac(track)
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection)
        report = relink_library(connection, source, library)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert report.applied is False
    assert len(report.matches) == 1
    assert report.matches[0].action == "copy"
    assert report.matches[0].destination_path == (
        library / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    ).resolve()
    assert row["local_path"] is None
    assert row["sha256"] is None
    assert row["status"] == "unresolved"
    assert not library.exists()


def test_apply_external_library_copies_and_records_without_deleting_source(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    track = source / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    _write_flac(track)
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection)
        report = relink_library(connection, source, library, apply=True)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    destination = library / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    assert len(report.matches) == 1
    assert report.matches[0].action == "copy"
    assert track.exists()
    assert destination.exists()
    assert destination.read_bytes() == track.read_bytes()
    assert Path(row["local_path"]) == destination.resolve()
    assert row["sha256"] == file_sha256(destination)
    assert row["status"] == "local"


def test_apply_canonical_tree_adopts_in_place(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    track = library / "Radiohead" / "song.flac"
    _write_flac(track)
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection)
        report = relink_library(connection, library, library, apply=True)
        row = connection.execute(
            "SELECT local_path, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert len(report.matches) == 1
    assert report.matches[0].action == "adopt"
    assert Path(row["local_path"]) == track.resolve()
    assert row["status"] == "local"


def test_duplicate_source_files_remain_ambiguous(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    _write_flac(source / "a.flac")
    _write_flac(source / "b.flac")
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection)
        report = relink_library(connection, source, library)

    assert report.matches == ()
    assert len(report.unmatched) == 1
    assert "multiple exact-ISRC candidates" in report.unmatched[0].reason


def test_one_source_file_is_not_assigned_to_two_tracks(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    _write_flac(source / "song.flac")
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection, "spotify-1")
        _desired_track(connection, "spotify-2")
        report = relink_library(connection, source, library)

    assert report.matches == ()
    assert len(report.unmatched) == 2
    assert all("multiple desired tracks" in item.reason for item in report.unmatched)


def test_existing_usable_canonical_track_is_skipped(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    local = library / "already.flac"
    _write_flac(local)
    _write_flac(source / "duplicate.flac")
    initialize(db)
    with connect(db) as connection:
        _desired_track(
            connection,
            local_path=str(local.resolve()),
            sha256=file_sha256(local),
        )
        report = relink_library(connection, source, library)

    assert report.already_local == 1
    assert report.target_tracks == 0
    assert report.matches == ()


def test_destination_collision_uses_deterministic_safe_filename(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    external = source / "Radiohead" / "song.flac"
    _write_flac(external)
    occupied = library / "Radiohead" / "song.flac"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"different-file")
    initialize(db)
    with connect(db) as connection:
        _desired_track(connection)
        report = relink_library(connection, source, library, apply=True)
        row = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    expected = library / "Radiohead" / "song [synctify-spotify-].flac"
    assert occupied.read_bytes() == b"different-file"
    assert expected.exists()
    assert Path(row["local_path"]) == expected.resolve()
    assert report.matches[0].destination_path == expected.resolve()


def test_cli_requires_existing_desired_state_database(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SYNCTIFY_HOME", str(tmp_path / "home"))
    source = tmp_path / "copied"
    source.mkdir()

    result = CliRunner().invoke(app, ["relink", str(source)])

    assert result.exit_code == 2
    assert "spotify pull" in result.output
