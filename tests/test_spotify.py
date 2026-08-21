from __future__ import annotations

from pathlib import Path

from synctify.db import connect, initialize
from synctify.spotify.auth import SpotifyOAuthConfig, code_challenge
from synctify.spotify.ingest import (
    LIKED_SONGS_ID,
    PlaylistEntry,
    SpotifyPlaylist,
    SpotifySnapshot,
    SpotifyTrack,
    fetch_spotify_snapshot,
    parse_track,
)
from synctify.spotify.state import apply_snapshot, plan_snapshot

RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _raw_track(track_id: str, name: str = "Track") -> dict[str, object]:
    return {
        "id": track_id,
        "name": name,
        "type": "track",
        "is_local": False,
        "duration_ms": 123000,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "external_ids": {"isrc": f"ISRC-{track_id}"},
    }


def _track(track_id: str) -> SpotifyTrack:
    return SpotifyTrack(track_id, f"Track {track_id}", "Artist", "Album", 123000, f"ISRC-{track_id}")


def test_pkce_matches_rfc_example() -> None:
    assert code_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_spotify_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "spotify.json"
    config = SpotifyOAuthConfig("client-123", "http://127.0.0.1:8765/callback")
    config.save(path)
    assert SpotifyOAuthConfig.load(path) == config


def test_parse_track_keeps_isrc_and_multiple_artists() -> None:
    raw = _raw_track("abc")
    raw["artists"] = [{"name": "One"}, {"name": "Two"}]
    parsed = parse_track(raw)
    assert parsed is not None
    assert parsed.spotify_id == "abc"
    assert parsed.artist == "One, Two"
    assert parsed.isrc == "ISRC-abc"


def test_parse_track_rejects_local_tracks() -> None:
    raw = _raw_track("abc")
    raw["is_local"] = True
    assert parse_track(raw) is None


class FakeSpotifyClient:
    def me(self) -> dict[str, object]:
        return {"id": "me"}

    def saved_tracks(self):
        yield {"added_at": "2026-08-20T00:00:00Z", "track": _raw_track("liked")}

    def current_user_playlists(self):
        yield {"id": "mine", "name": "Mine", "owner": {"id": "me"}, "collaborative": False, "snapshot_id": "snap-1"}
        yield {"id": "followed", "name": "Followed", "owner": {"id": "someone-else"}, "collaborative": False, "snapshot_id": "ignored"}

    def playlist_items(self, playlist_id: str):
        assert playlist_id == "mine"
        yield {"added_at": "2026-08-20T01:00:00Z", "item": _raw_track("playlist")}


def test_fetch_snapshot_uses_new_playlist_item_field_and_filters_followed() -> None:
    snapshot = fetch_spotify_snapshot(FakeSpotifyClient())
    assert snapshot.user_id == "me"
    assert [playlist.spotify_id for playlist in snapshot.playlists] == [LIKED_SONGS_ID, "mine"]
    assert snapshot.playlists[1].tracks[0].track.spotify_id == "playlist"


def test_plan_and_apply_snapshot_preserves_track_acquisition_fields(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    first = SpotifySnapshot(
        user_id="me",
        playlists=(
            SpotifyPlaylist(LIKED_SONGS_ID, "Liked Songs", (PlaylistEntry(_track("a")), PlaylistEntry(_track("b"))), "one", "liked", "me"),
            SpotifyPlaylist("playlist", "Old Name", (PlaylistEntry(_track("a")), PlaylistEntry(_track("c"))), "p1", "playlist", "me"),
        ),
    )
    with connect(database) as connection:
        initial_plan = plan_snapshot(connection, first)
        assert initial_plan.tracks_added == 4
        apply_snapshot(connection, first)
        connection.execute("UPDATE tracks SET qobuz_id='q-1', local_path='/music/a.flac', status='resolved' WHERE spotify_id='a'")
        connection.commit()

    second = SpotifySnapshot(
        user_id="me",
        playlists=(
            SpotifyPlaylist(LIKED_SONGS_ID, "Liked Songs", (PlaylistEntry(_track("b")), PlaylistEntry(_track("a"))), "two", "liked", "me"),
            SpotifyPlaylist("playlist", "New Name", (PlaylistEntry(_track("a")), PlaylistEntry(_track("d"))), "p2", "playlist", "me"),
        ),
    )
    with connect(database) as connection:
        plan = plan_snapshot(connection, second)
        assert plan.tracks_added == 1
        assert plan.tracks_removed == 1
        assert plan.playlists_renamed == (("Old Name", "New Name"),)
        assert plan.playlists_reordered == ("Liked Songs",)
        apply_snapshot(connection, second)
        preserved = connection.execute("SELECT qobuz_id, local_path, status FROM tracks WHERE spotify_id='a'").fetchone()
        assert tuple(preserved) == ("q-1", "/music/a.flac", "resolved")
