from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable

from .client import SpotifyAPIError, SpotifyClient

LIKED_SONGS_ID = "synctify:spotify:liked-songs"


@dataclass(slots=True, frozen=True)
class SpotifyTrack:
    spotify_id: str
    title: str
    artist: str
    album: str | None
    duration_ms: int | None
    isrc: str | None


@dataclass(slots=True, frozen=True)
class PlaylistEntry:
    track: SpotifyTrack
    added_at: str | None = None


@dataclass(slots=True, frozen=True)
class SpotifyPlaylist:
    spotify_id: str
    name: str
    tracks: tuple[PlaylistEntry, ...]
    snapshot_id: str | None
    source_kind: str
    owner_id: str | None = None
    collaborative: bool = False


@dataclass(slots=True, frozen=True)
class SpotifySnapshot:
    user_id: str
    playlists: tuple[SpotifyPlaylist, ...]
    skipped_items: int = 0
    inaccessible_playlists: tuple[str, ...] = ()


def parse_track(raw: dict[str, Any] | None) -> SpotifyTrack | None:
    if not raw or raw.get("type") != "track" or raw.get("is_local") is True:
        return None
    spotify_id = raw.get("id")
    title = raw.get("name")
    if not isinstance(spotify_id, str) or not isinstance(title, str):
        return None
    artists = raw.get("artists") or []
    artist_names = [artist.get("name") for artist in artists if isinstance(artist, dict) and isinstance(artist.get("name"), str)]
    if not artist_names:
        return None
    album_obj = raw.get("album")
    album = album_obj.get("name") if isinstance(album_obj, dict) else None
    external_ids = raw.get("external_ids")
    isrc = external_ids.get("isrc") if isinstance(external_ids, dict) else None
    duration = raw.get("duration_ms")
    return SpotifyTrack(spotify_id, title, ", ".join(artist_names), album if isinstance(album, str) else None, duration if isinstance(duration, int) else None, isrc if isinstance(isrc, str) else None)


def _parse_entries(items: Iterable[dict[str, Any]], *, playlist_items: bool) -> tuple[tuple[PlaylistEntry, ...], int]:
    entries: list[PlaylistEntry] = []
    skipped = 0
    for wrapper in items:
        raw_track = wrapper.get("item") if playlist_items else wrapper.get("track")
        if playlist_items and raw_track is None:
            raw_track = wrapper.get("track")
        track = parse_track(raw_track if isinstance(raw_track, dict) else None)
        if track is None:
            skipped += 1
            continue
        added_at = wrapper.get("added_at")
        entries.append(PlaylistEntry(track, added_at if isinstance(added_at, str) else None))
    return tuple(entries), skipped


def _liked_snapshot_id(entries: tuple[PlaylistEntry, ...]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.track.spotify_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update((entry.added_at or "").encode("utf-8"))
        digest.update(b"\n")
    return f"synctify:{digest.hexdigest()}"


def fetch_spotify_snapshot(client: SpotifyClient) -> SpotifySnapshot:
    profile = client.me()
    user_id = profile.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise SpotifyAPIError(200, "current user response did not include an id")
    liked_entries, skipped = _parse_entries(client.saved_tracks(), playlist_items=False)
    playlists = [SpotifyPlaylist(LIKED_SONGS_ID, "Liked Songs", liked_entries, _liked_snapshot_id(liked_entries), "liked", user_id)]
    inaccessible: list[str] = []
    for playlist in client.current_user_playlists():
        playlist_id = playlist.get("id")
        name = playlist.get("name")
        owner = playlist.get("owner")
        owner_id = owner.get("id") if isinstance(owner, dict) else None
        collaborative = playlist.get("collaborative") is True
        if not isinstance(playlist_id, str) or not isinstance(name, str):
            continue
        if owner_id != user_id and not collaborative:
            continue
        try:
            entries, skipped_here = _parse_entries(client.playlist_items(playlist_id), playlist_items=True)
        except SpotifyAPIError as exc:
            if exc.status_code == 403:
                inaccessible.append(playlist_id)
                continue
            raise
        skipped += skipped_here
        raw_snapshot_id = playlist.get("snapshot_id")
        playlists.append(SpotifyPlaylist(playlist_id, name, entries, raw_snapshot_id if isinstance(raw_snapshot_id, str) else None, "playlist", owner_id if isinstance(owner_id, str) else None, collaborative))
    return SpotifySnapshot(user_id, tuple(playlists), skipped, tuple(inaccessible))
