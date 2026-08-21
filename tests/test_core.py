from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from synctify.db import initialize
from synctify.models import Playlist, Track
from synctify.playlists import MissingLocalTrackError, render_m3u8, safe_playlist_filename
from synctify.sync import SyncMode, SyncTarget, destination_deletes_enabled, rclone_command


def test_initialize_creates_schema(tmp_path: Path) -> None:
    database = tmp_path / "state" / "synctify.sqlite3"
    initialize(database)

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        schema_version = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]

    assert {"tracks", "playlists", "playlist_tracks", "sync_targets", "sync_runs"} <= tables
    assert schema_version == "1"


def test_m3u8_uses_relative_utf8_paths(tmp_path: Path) -> None:
    playlist_dir = tmp_path / "Playlists"
    track_path = tmp_path / "Artists" / "宇多田ヒカル" / "First Love" / "01 - Automatic.flac"
    playlist = Playlist(
        spotify_id="playlist-1",
        name="日本語 / Favorites",
        tracks=(
            Track(
                spotify_id="track-1",
                title="Automatic",
                artist="宇多田ヒカル",
                local_path=track_path,
            ),
        ),
    )

    content = render_m3u8(playlist, playlist_dir)

    assert content.startswith("#EXTM3U\n")
    assert "../Artists/宇多田ヒカル/First Love/01 - Automatic.flac" in content
    assert safe_playlist_filename(playlist.name) == "日本語 _ Favorites"


def test_m3u8_rejects_unresolved_track(tmp_path: Path) -> None:
    playlist = Playlist(
        spotify_id="playlist-1",
        name="Broken",
        tracks=(Track(spotify_id="track-1", title="Missing", artist="Artist"),),
    )

    with pytest.raises(MissingLocalTrackError):
        render_m3u8(playlist, tmp_path)


def test_sync_modes_have_different_delete_semantics(tmp_path: Path) -> None:
    mirror = SyncTarget("phone", "/Volumes/Phone/Music", SyncMode.MIRROR)
    backup = SyncTarget("pcloud", "pcloud:Synctify", SyncMode.BACKUP)

    assert rclone_command(tmp_path, mirror)[1] == "sync"
    assert rclone_command(tmp_path, backup)[1] == "copy"
    assert destination_deletes_enabled(SyncMode.MIRROR) is True
    assert destination_deletes_enabled(SyncMode.BACKUP) is False
