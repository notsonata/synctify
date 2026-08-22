from __future__ import annotations

from pathlib import Path

from synctify.db import connect, initialize
from synctify.spotify.ingest import LIKED_SONGS_ID, PlaylistEntry, SpotifyTrack
from synctify.spotify.selection import (
    PlaylistCatalogEntry,
    apply_playlist_choices,
    confirm_playlist,
    fetch_playlist_catalog,
    list_playlist_catalog,
    list_playlist_items,
    refresh_playlist_items,
    set_item_choice,
    store_playlist_catalog,
    unimport_playlist,
)


def _track(track_id: str, title: str) -> PlaylistEntry:
    return PlaylistEntry(
        SpotifyTrack(
            spotify_id=track_id,
            title=title,
            artist="Artist",
            album="Album",
            duration_ms=180000,
            isrc=f"USAAA26{track_id[-5:].zfill(5)}",
        ),
        added_at=f"2026-08-23T00:00:{track_id[-1].zfill(2)}Z",
    )


def _catalog(playlist_id: str = "playlist-1", name: str = "Playlist One") -> PlaylistCatalogEntry:
    return PlaylistCatalogEntry(
        spotify_id=playlist_id,
        name=name,
        source_kind="playlist",
        snapshot_id="snap-1",
        owner_id="user-1",
        collaborative=False,
        track_count=3,
    )


def test_fetch_catalog_includes_liked_songs_without_fetching_tracks() -> None:
    class FakeClient:
        def me(self):
            return {"id": "user-1"}

        def current_user_playlists(self):
            yield {
                "id": "playlist-1",
                "name": "Playlist One",
                "owner": {"id": "user-1"},
                "collaborative": False,
                "snapshot_id": "snap-1",
                "tracks": {"total": 42},
            }
            yield {
                "id": "followed-1",
                "name": "Someone Else",
                "owner": {"id": "other"},
                "collaborative": False,
                "snapshot_id": "snap-x",
                "tracks": {"total": 1000},
            }

        def saved_tracks(self):
            raise AssertionError("catalog fetch must not fetch Liked Songs tracks")

        def playlist_items(self, _playlist_id: str):
            raise AssertionError("catalog fetch must not fetch playlist tracks")

    user_id, entries = fetch_playlist_catalog(FakeClient())  # type: ignore[arg-type]

    assert user_id == "user-1"
    assert [entry.spotify_id for entry in entries] == [LIKED_SONGS_ID, "playlist-1"]
    assert entries[0].name == "Liked Songs"
    assert entries[1].track_count == 42


def test_exclusions_persist_and_new_tracks_stay_pending(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        store_playlist_catalog(connection, "user-1", (_catalog(),))
        refresh_playlist_items(connection, "playlist-1", (_track("track-1", "One"), _track("track-2", "Two")))

        initial = list_playlist_items(connection, "playlist-1")
        assert [item.state for item in initial] == ["pending_add", "pending_add"]

        track_two = next(item for item in initial if item.track_id == "track-2")
        set_item_choice(connection, "playlist-1", track_two.item_key, "excluded")
        confirm_playlist(connection, "playlist-1")

        active = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = 'playlist-1' ORDER BY position"
        ).fetchall()
        assert [row["track_id"] for row in active] == ["track-1"]

        refresh_playlist_items(
            connection,
            "playlist-1",
            (_track("track-1", "One"), _track("track-2", "Two"), _track("track-3", "Three")),
        )
        states = {item.track_id: item.state for item in list_playlist_items(connection, "playlist-1")}
        assert states == {
            "track-1": "included",
            "track-2": "excluded",
            "track-3": "pending_add",
        }

        catalog = list_playlist_catalog(connection)[0]
        assert catalog.tracked
        assert catalog.included == 1
        assert catalog.excluded == 1
        assert catalog.pending_add == 1

        active = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = 'playlist-1' ORDER BY position"
        ).fetchall()
        assert [row["track_id"] for row in active] == ["track-1"]


def test_tracked_removals_are_pending_until_reviewed(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        store_playlist_catalog(connection, "user-1", (_catalog(),))
        refresh_playlist_items(
            connection,
            "playlist-1",
            (_track("track-1", "One"), _track("track-2", "Two")),
        )
        confirm_playlist(connection, "playlist-1")

        refresh_playlist_items(connection, "playlist-1", (_track("track-2", "Two"),))
        items = {item.track_id: item for item in list_playlist_items(connection, "playlist-1")}
        assert items["track-1"].state == "pending_remove"
        assert not items["track-1"].present

        # Pending removal does not mutate the active desired playlist.
        before = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = 'playlist-1' ORDER BY position"
        ).fetchall()
        assert [row["track_id"] for row in before] == ["track-1", "track-2"]

        set_item_choice(connection, "playlist-1", items["track-1"].item_key, "excluded")
        apply_playlist_choices(connection, "playlist-1")
        after = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = 'playlist-1' ORDER BY position"
        ).fetchall()
        assert [row["track_id"] for row in after] == ["track-2"]


def test_unimport_deletes_flac_only_after_last_playlist_reference(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    library = tmp_path / "library"
    playlists_dir = tmp_path / "playlists"
    library.mkdir()
    playlists_dir.mkdir()
    flac = library / "Artist" / "Album" / "Song.flac"
    flac.parent.mkdir(parents=True)
    flac.write_bytes(b"flac")

    initialize(database)
    p1 = _catalog("playlist-1", "One")
    p2 = _catalog("playlist-2", "Two")
    entry = _track("track-1", "Song")
    with connect(database) as connection:
        store_playlist_catalog(connection, "user-1", (p1, p2))
        for playlist_id in ("playlist-1", "playlist-2"):
            refresh_playlist_items(connection, playlist_id, (entry,))
            confirm_playlist(connection, playlist_id)
        connection.execute(
            "UPDATE tracks SET local_path = ?, sha256 = 'abc', status = 'downloaded' WHERE spotify_id = 'track-1'",
            (str(flac),),
        )

        first = unimport_playlist(connection, "playlist-1", library, playlists_dir)
        assert flac.exists()
        assert first.cleanup.deleted == ()

        second = unimport_playlist(connection, "playlist-2", library, playlists_dir)
        assert not flac.exists()
        assert [item.spotify_id for item in second.cleanup.deleted] == ["track-1"]


def test_schema_migrates_existing_imports_into_tracked_catalog(tmp_path: Path) -> None:
    database = tmp_path / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, status) VALUES ('track-1', 'Song', 'Artist', 'unresolved')"
        )
        connection.execute(
            "INSERT INTO playlists(spotify_id, name, source_kind, collaborative) VALUES ('playlist-1', 'Legacy', 'playlist', 0)"
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', 'track-1', 0)"
        )

    # Running initialize again represents upgrading an existing installation.
    initialize(database)
    with connect(database) as connection:
        catalog = {item.spotify_id: item for item in list_playlist_catalog(connection)}
        assert catalog["playlist-1"].tracked
        items = list_playlist_items(connection, "playlist-1")
        assert len(items) == 1
        assert items[0].state == "included"
