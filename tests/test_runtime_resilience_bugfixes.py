from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

import httpx
import pytest

from synctify.backup import add_rclone_backup_target, run_rclone_backup
from synctify.db import connect, initialize
from synctify.spotify.client import SpotifyAPIError, SpotifyClient


class _Auth:
    def access_token(self, *, force_refresh: bool = False) -> str:
        return "token"


class _FailingHTTPClient:
    def get(self, url: str, **kwargs: object):
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("DNS lookup failed", request=request)


def test_spotify_transport_error_is_normalized_to_api_error() -> None:
    client = SpotifyClient(_Auth(), http_client=_FailingHTTPClient())  # type: ignore[arg-type]

    with pytest.raises(SpotifyAPIError, match="Spotify request failed") as caught:
        client.me()

    assert caught.value.status_code == 0
    assert "DNS lookup failed" in caught.value.message


def test_changed_playlist_same_second_gets_unique_snapshot_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    library.mkdir()
    playlists.mkdir()
    (library / "track.flac").write_bytes(b"flac")
    playlist = playlists / "Driving.m3u8"
    playlist.write_text("#EXTM3U\n../library/track.flac\n", encoding="utf-8")
    initialize(database)
    monkeypatch.setattr("synctify.backup.shutil.which", lambda _: "/usr/local/bin/rclone")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    same_second = datetime(2026, 8, 22, 9, 30, 15, tzinfo=timezone.utc)
    with connect(database) as connection:
        target = add_rclone_backup_target(connection, "cloud", "pcloud:Synctify")
        first = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=same_second,
        )
        playlist.write_text(
            "#EXTM3U\n../library/track.flac\n../library/other.flac\n",
            encoding="utf-8",
        )
        second = run_rclone_backup(
            connection,
            target,
            library_dir=library,
            playlists_dir=playlists,
            runner=runner,
            now=same_second,
        )
        paths = [
            row["remote_path"]
            for row in connection.execute(
                "SELECT remote_path FROM playlist_backup_snapshots ORDER BY id"
            ).fetchall()
        ]

    assert len(first.snapshots) == 1
    assert len(second.snapshots) == 1
    assert len(paths) == 2
    assert paths[0].endswith("2026-08-22T093015Z.m3u8")
    assert paths[1] != paths[0]
    assert "2026-08-22T093015Z-" in paths[1]
    assert paths[1].endswith(".m3u8")
