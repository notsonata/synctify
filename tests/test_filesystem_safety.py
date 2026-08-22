from __future__ import annotations

from pathlib import Path

import pytest

from synctify.db import connect, initialize
from synctify.gc import clean_unreferenced_tracks
from synctify.sync import (
    SyncMode,
    SyncTarget,
    UnsafeSyncTargetError,
    add_filesystem_target,
    validate_filesystem_target,
)


def _insert_track(connection, spotify_id: str, path: Path) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, title, artist, album, duration_ms, local_path, sha256, status)
        VALUES (?, ?, 'Artist', 'Album', 200000, ?, 'hash', 'local')
        """,
        (spotify_id, spotify_id, str(path)),
    )


def _reference_track(connection, spotify_id: str) -> None:
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES ('playlist-1', 'Playlist')"
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', ?, 0)",
        (spotify_id,),
    )


def test_clean_never_follows_internal_symlink(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    real_track = library / "Artist" / "Album" / "Real.flac"
    real_track.parent.mkdir(parents=True)
    real_track.write_bytes(b"real audio")
    orphan_link = library / "Orphan.flac"
    orphan_link.symlink_to(real_track)
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-used", real_track)
        _reference_track(connection, "spotify-used")
        _insert_track(connection, "spotify-orphan", orphan_link)

        report = clean_unreferenced_tracks(connection, library, apply=True)
        orphan = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'spotify-orphan'"
        ).fetchone()

    assert real_track.exists()
    assert orphan_link.is_symlink()
    assert report.deleted == ()
    assert len(report.skipped) == 1
    assert "symbolic link" in (report.skipped[0].reason or "")
    assert orphan["local_path"] == str(orphan_link)


def test_relative_mirror_target_is_frozen_to_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_destination = first_cwd / "device"
    second_destination = second_cwd / "device"
    first_destination.mkdir(parents=True)
    second_destination.mkdir(parents=True)
    initialize(database)

    monkeypatch.chdir(first_cwd)
    with connect(database) as connection:
        target = add_filesystem_target(connection, "phone", Path("device"))

    assert target.destination == str(first_destination.resolve())

    monkeypatch.chdir(second_cwd)
    resolved = validate_filesystem_target(target, tmp_path / "home")

    assert resolved == first_destination.resolve()
    assert resolved != second_destination.resolve()


def test_legacy_relative_mirror_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "device"
    destination.mkdir()
    monkeypatch.chdir(tmp_path)
    target = SyncTarget("phone", "device", SyncMode.MIRROR)

    with pytest.raises(UnsafeSyncTargetError, match="relative and unsafe"):
        validate_filesystem_target(target, tmp_path / "home")
