from __future__ import annotations

from pathlib import Path

from synctify.db import connect, initialize
from synctify.spotify.ingest import PlaylistEntry, SpotifyPlaylist, SpotifySnapshot, SpotifyTrack
from synctify.spotify.state import apply_snapshot, plan_snapshot


def _playlist(playlist_id: str, name: str, track_id: str) -> SpotifyPlaylist:
    track = SpotifyTrack(
        spotify_id=track_id,
        title=f"Track {track_id}",
        artist="Artist",
        album="Album",
        duration_ms=180_000,
        isrc=None,
    )
    return SpotifyPlaylist(
        spotify_id=playlist_id,
        name=name,
        tracks=(PlaylistEntry(track),),
        snapshot_id="snapshot",
        source_kind="playlist",
        owner_id="user",
    )


def test_inaccessible_playlist_is_not_planned_or_applied_as_removed(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)

    initial = SpotifySnapshot(
        user_id="user",
        playlists=(
            _playlist("playlist-visible", "Visible", "track-visible"),
            _playlist("playlist-403", "Temporarily inaccessible", "track-403"),
        ),
    )
    refresh = SpotifySnapshot(
        user_id="user",
        playlists=(_playlist("playlist-visible", "Visible", "track-visible"),),
        inaccessible_playlists=("playlist-403",),
    )

    with connect(database) as connection:
        apply_snapshot(connection, initial)
        plan = plan_snapshot(connection, refresh)
        apply_snapshot(connection, refresh)

        preserved = connection.execute(
            "SELECT name FROM playlists WHERE spotify_id = ?",
            ("playlist-403",),
        ).fetchone()
        memberships = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?",
            ("playlist-403",),
        ).fetchall()

    assert plan.playlists_removed == ()
    assert plan.tracks_removed == 0
    assert preserved is not None
    assert preserved["name"] == "Temporarily inaccessible"
    assert [row["track_id"] for row in memberships] == ["track-403"]
