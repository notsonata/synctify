from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from ..gc import CleanupReport, clean_unreferenced_tracks
from ..playlists import PlaylistBuildReport, build_playlists
from .client import SpotifyAPIError, SpotifyClient
from .ingest import LIKED_SONGS_ID, PlaylistEntry, parse_track

CancelCheck = Callable[[], bool]
ProgressReporter = Callable[[str], None]


class SpotifySelectionCancelled(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class PlaylistCatalogEntry:
    spotify_id: str
    name: str
    source_kind: str
    snapshot_id: str | None
    owner_id: str | None
    collaborative: bool
    track_count: int | None
    tracked: bool = False
    available: bool = True
    included: int = 0
    excluded: int = 0
    pending_add: int = 0
    pending_remove: int = 0


@dataclass(slots=True, frozen=True)
class PlaylistItemState:
    playlist_id: str
    item_key: str
    track_id: str
    position: int
    added_at: str | None
    title: str
    artist: str
    album: str | None
    duration_ms: int | None
    isrc: str | None
    state: str
    present: bool


@dataclass(slots=True, frozen=True)
class UnimportReport:
    cleanup: CleanupReport
    playlists: PlaylistBuildReport


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_cancel(cancelled: CancelCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise SpotifySelectionCancelled("Spotify operation cancelled")


def _notify(progress: ProgressReporter | None, message: str) -> None:
    if progress is not None:
        progress(message)


def fetch_playlist_catalog(
    client: SpotifyClient,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[str, tuple[PlaylistCatalogEntry, ...]]:
    _check_cancel(cancelled)
    _notify(progress, "Fetching Spotify profile…")
    profile = client.me()
    user_id = profile.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise SpotifyAPIError(200, "current user response did not include an id")

    entries: list[PlaylistCatalogEntry] = [
        PlaylistCatalogEntry(
            spotify_id=LIKED_SONGS_ID,
            name="Liked Songs",
            source_kind="liked",
            snapshot_id=None,
            owner_id=user_id,
            collaborative=False,
            track_count=None,
        )
    ]
    _notify(progress, "Fetching Spotify playlist list…")
    for raw in client.current_user_playlists():
        _check_cancel(cancelled)
        playlist_id = raw.get("id")
        name = raw.get("name")
        owner = raw.get("owner")
        owner_id = owner.get("id") if isinstance(owner, dict) else None
        collaborative = raw.get("collaborative") is True
        if not isinstance(playlist_id, str) or not isinstance(name, str):
            continue
        snapshot_id = raw.get("snapshot_id")
        tracks = raw.get("tracks")
        total = tracks.get("total") if isinstance(tracks, dict) else None
        entries.append(
            PlaylistCatalogEntry(
                spotify_id=playlist_id,
                name=name,
                source_kind="playlist",
                snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
                owner_id=owner_id if isinstance(owner_id, str) else None,
                collaborative=collaborative,
                track_count=total if isinstance(total, int) else None,
            )
        )
    _notify(progress, f"Fetched {len(entries)} Spotify playlist(s).")
    return user_id, tuple(entries)


def store_playlist_catalog(
    connection: sqlite3.Connection,
    user_id: str,
    entries: Iterable[PlaylistCatalogEntry],
) -> None:
    fetched_at = _now()
    ids: set[str] = set()
    for entry in entries:
        ids.add(entry.spotify_id)
        connection.execute(
            """
            INSERT INTO spotify_playlist_catalog(
                spotify_id, name, snapshot_id, source_kind, owner_id,
                collaborative, track_count, tracked, available, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                name=excluded.name,
                snapshot_id=excluded.snapshot_id,
                source_kind=excluded.source_kind,
                owner_id=excluded.owner_id,
                collaborative=excluded.collaborative,
                track_count=excluded.track_count,
                available=1,
                fetched_at=excluded.fetched_at
            """,
            (
                entry.spotify_id,
                entry.name,
                entry.snapshot_id,
                entry.source_kind,
                entry.owner_id,
                int(entry.collaborative),
                entry.track_count,
                fetched_at,
            ),
        )
    connection.execute("UPDATE spotify_playlist_catalog SET available = 0")
    if ids:
        placeholders = ",".join("?" for _ in ids)
        connection.execute(
            f"UPDATE spotify_playlist_catalog SET available = 1 WHERE spotify_id IN ({placeholders})",
            tuple(ids),
        )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('spotify_user_id', ?)",
        (user_id,),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('spotify_catalog_fetched_at', ?)",
        (fetched_at,),
    )


def list_playlist_catalog(connection: sqlite3.Connection) -> tuple[PlaylistCatalogEntry, ...]:
    rows = connection.execute(
        """
        SELECT
            c.spotify_id, c.name, c.source_kind, c.snapshot_id, c.owner_id,
            c.collaborative, c.track_count, c.tracked, c.available,
            SUM(CASE WHEN i.state = 'included' THEN 1 ELSE 0 END) AS included,
            SUM(CASE WHEN i.state = 'excluded' THEN 1 ELSE 0 END) AS excluded,
            SUM(CASE WHEN i.state = 'pending_add' THEN 1 ELSE 0 END) AS pending_add,
            SUM(CASE WHEN i.state = 'pending_remove' THEN 1 ELSE 0 END) AS pending_remove
        FROM spotify_playlist_catalog AS c
        LEFT JOIN spotify_playlist_items AS i ON i.playlist_id = c.spotify_id
        GROUP BY c.spotify_id
        ORDER BY c.tracked DESC, c.name COLLATE NOCASE, c.spotify_id
        """
    ).fetchall()
    return tuple(
        PlaylistCatalogEntry(
            spotify_id=row["spotify_id"],
            name=row["name"],
            source_kind=row["source_kind"],
            snapshot_id=row["snapshot_id"],
            owner_id=row["owner_id"],
            collaborative=bool(row["collaborative"]),
            track_count=row["track_count"],
            tracked=bool(row["tracked"]),
            available=bool(row["available"]),
            included=int(row["included"] or 0),
            excluded=int(row["excluded"] or 0),
            pending_add=int(row["pending_add"] or 0),
            pending_remove=int(row["pending_remove"] or 0),
        )
        for row in rows
    )


def _item_key(track_id: str, added_at: str | None, occurrence: int) -> str:
    base = f"{track_id}:{added_at or 'no-added-at'}"
    return base if occurrence == 0 else f"{base}#{occurrence + 1}"


def _records(entries: Iterable[PlaylistEntry]) -> list[tuple[str, int, PlaylistEntry]]:
    counts: dict[tuple[str, str | None], int] = {}
    result: list[tuple[str, int, PlaylistEntry]] = []
    for position, entry in enumerate(entries):
        identity = (entry.track.spotify_id, entry.added_at)
        occurrence = counts.get(identity, 0)
        counts[identity] = occurrence + 1
        result.append((_item_key(entry.track.spotify_id, entry.added_at, occurrence), position, entry))
    return result


def _parse_entries_cancellable(
    items: Iterable[dict[str, Any]],
    *,
    playlist_items: bool,
    cancelled: CancelCheck | None,
    progress: ProgressReporter | None,
    playlist_name: str,
) -> tuple[PlaylistEntry, ...]:
    entries: list[PlaylistEntry] = []
    for index, wrapper in enumerate(items, start=1):
        _check_cancel(cancelled)
        raw_track = wrapper.get("item") if playlist_items else wrapper.get("track")
        if playlist_items and raw_track is None:
            raw_track = wrapper.get("track")
        track = parse_track(raw_track if isinstance(raw_track, dict) else None)
        if track is not None:
            added_at = wrapper.get("added_at")
            entries.append(
                PlaylistEntry(
                    track,
                    added_at if isinstance(added_at, str) else None,
                )
            )
        if index % 50 == 0:
            _notify(progress, f"Fetching {playlist_name}… {index} item(s) read")
    _check_cancel(cancelled)
    return tuple(entries)


def fetch_one_playlist(
    client: SpotifyClient,
    playlist: PlaylistCatalogEntry,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[PlaylistEntry, ...]:
    _check_cancel(cancelled)
    _notify(progress, f"Fetching {playlist.name}…")
    source = (
        client.saved_tracks()
        if playlist.source_kind == "liked"
        else client.playlist_items(playlist.spotify_id)
    )
    parsed = _parse_entries_cancellable(
        source,
        playlist_items=playlist.source_kind != "liked",
        cancelled=cancelled,
        progress=progress,
        playlist_name=playlist.name,
    )
    _notify(progress, f"Fetched {len(parsed)} track(s) from {playlist.name}.")
    return parsed


def refresh_playlist_items(
    connection: sqlite3.Connection,
    playlist_id: str,
    entries: Iterable[PlaylistEntry],
) -> None:
    catalog = connection.execute(
        "SELECT tracked FROM spotify_playlist_catalog WHERE spotify_id = ?",
        (playlist_id,),
    ).fetchone()
    if catalog is None:
        raise KeyError(f"unknown Spotify playlist: {playlist_id}")
    tracked = bool(catalog["tracked"])
    old_rows = connection.execute(
        "SELECT item_key, state FROM spotify_playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchall()
    old_state = {row["item_key"]: row["state"] for row in old_rows}
    seen: set[str] = set()

    for key, position, entry in _records(entries):
        seen.add(key)
        previous = old_state.get(key)
        if previous == "excluded":
            state = "excluded"
        elif previous in {"included", "pending_remove"}:
            state = "included"
        elif previous == "pending_add":
            state = "pending_add"
        else:
            state = "pending_add"
        track = entry.track
        connection.execute(
            """
            INSERT INTO spotify_playlist_items(
                playlist_id, item_key, track_id, position, added_at, title, artist,
                album, duration_ms, isrc, state, present
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(playlist_id, item_key) DO UPDATE SET
                track_id=excluded.track_id,
                position=excluded.position,
                added_at=excluded.added_at,
                title=excluded.title,
                artist=excluded.artist,
                album=excluded.album,
                duration_ms=excluded.duration_ms,
                isrc=excluded.isrc,
                state=excluded.state,
                present=1
            """,
            (
                playlist_id,
                key,
                track.spotify_id,
                position,
                entry.added_at,
                track.title,
                track.artist,
                track.album,
                track.duration_ms,
                track.isrc,
                state,
            ),
        )

    for row in connection.execute(
        "SELECT item_key, state FROM spotify_playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchall():
        key = row["item_key"]
        if key in seen:
            continue
        state = row["state"]
        if tracked and state in {"included", "pending_remove"}:
            connection.execute(
                "UPDATE spotify_playlist_items SET state = 'pending_remove', present = 0 WHERE playlist_id = ? AND item_key = ?",
                (playlist_id, key),
            )
        elif state == "excluded":
            connection.execute(
                "UPDATE spotify_playlist_items SET present = 0 WHERE playlist_id = ? AND item_key = ?",
                (playlist_id, key),
            )
        else:
            connection.execute(
                "DELETE FROM spotify_playlist_items WHERE playlist_id = ? AND item_key = ?",
                (playlist_id, key),
            )

    connection.execute(
        "UPDATE spotify_playlist_catalog SET items_fetched_at = ? WHERE spotify_id = ?",
        (_now(), playlist_id),
    )


def list_playlist_items(
    connection: sqlite3.Connection,
    playlist_id: str,
) -> tuple[PlaylistItemState, ...]:
    rows = connection.execute(
        """
        SELECT * FROM spotify_playlist_items
        WHERE playlist_id = ?
        ORDER BY present DESC, position, artist COLLATE NOCASE, title COLLATE NOCASE
        """,
        (playlist_id,),
    ).fetchall()
    return tuple(
        PlaylistItemState(
            playlist_id=row["playlist_id"],
            item_key=row["item_key"],
            track_id=row["track_id"],
            position=int(row["position"]),
            added_at=row["added_at"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            duration_ms=row["duration_ms"],
            isrc=row["isrc"],
            state=row["state"],
            present=bool(row["present"]),
        )
        for row in rows
    )


def set_item_choice(
    connection: sqlite3.Connection,
    playlist_id: str,
    item_key: str,
    choice: str,
) -> None:
    if choice not in {"included", "excluded"}:
        raise ValueError("choice must be included or excluded")
    cursor = connection.execute(
        "UPDATE spotify_playlist_items SET state = ? WHERE playlist_id = ? AND item_key = ?",
        (choice, playlist_id, item_key),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"unknown playlist item: {item_key}")


def _materialize_playlist(connection: sqlite3.Connection, playlist_id: str) -> None:
    catalog = connection.execute(
        "SELECT * FROM spotify_playlist_catalog WHERE spotify_id = ?",
        (playlist_id,),
    ).fetchone()
    if catalog is None:
        raise KeyError(f"unknown Spotify playlist: {playlist_id}")
    now = _now()
    connection.execute(
        """
        INSERT INTO playlists(
            spotify_id, name, snapshot_id, last_checked_at, source_kind, owner_id, collaborative
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            name=excluded.name,
            snapshot_id=excluded.snapshot_id,
            last_checked_at=excluded.last_checked_at,
            source_kind=excluded.source_kind,
            owner_id=excluded.owner_id,
            collaborative=excluded.collaborative
        """,
        (
            playlist_id,
            catalog["name"],
            catalog["snapshot_id"],
            now,
            catalog["source_kind"],
            catalog["owner_id"],
            catalog["collaborative"],
        ),
    )
    connection.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    rows = connection.execute(
        """
        SELECT * FROM spotify_playlist_items
        WHERE playlist_id = ? AND state = 'included'
        ORDER BY position
        """,
        (playlist_id,),
    ).fetchall()
    for position, row in enumerate(rows):
        connection.execute(
            """
            INSERT INTO tracks(spotify_id, isrc, title, artist, album, duration_ms, status)
            VALUES (?, ?, ?, ?, ?, ?, 'unresolved')
            ON CONFLICT(spotify_id) DO UPDATE SET
                isrc=COALESCE(excluded.isrc, tracks.isrc),
                title=excluded.title,
                artist=excluded.artist,
                album=excluded.album,
                duration_ms=excluded.duration_ms
            """,
            (
                row["track_id"],
                row["isrc"],
                row["title"],
                row["artist"],
                row["album"],
                row["duration_ms"],
            ),
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
            (playlist_id, row["track_id"], position, row["added_at"]),
        )


def confirm_playlist(connection: sqlite3.Connection, playlist_id: str) -> None:
    catalog = connection.execute(
        "SELECT tracked FROM spotify_playlist_catalog WHERE spotify_id = ?",
        (playlist_id,),
    ).fetchone()
    if catalog is None:
        raise KeyError(f"unknown Spotify playlist: {playlist_id}")
    if catalog["tracked"]:
        raise ValueError("playlist is already imported; review pending changes and use Apply Choices")
    if connection.execute(
        "SELECT 1 FROM spotify_playlist_items WHERE playlist_id = ? LIMIT 1",
        (playlist_id,),
    ).fetchone() is None:
        raise ValueError("load the playlist tracks before importing it")
    connection.execute(
        "UPDATE spotify_playlist_items SET state = 'included' WHERE playlist_id = ? AND state = 'pending_add'",
        (playlist_id,),
    )
    connection.execute(
        "UPDATE spotify_playlist_catalog SET tracked = 1 WHERE spotify_id = ?",
        (playlist_id,),
    )
    _materialize_playlist(connection, playlist_id)


def apply_playlist_choices(connection: sqlite3.Connection, playlist_id: str) -> None:
    tracked = connection.execute(
        "SELECT tracked FROM spotify_playlist_catalog WHERE spotify_id = ?",
        (playlist_id,),
    ).fetchone()
    if tracked is None or not tracked["tracked"]:
        raise ValueError("playlist is not imported into Synctify")
    _materialize_playlist(connection, playlist_id)


def unimport_playlist(
    connection: sqlite3.Connection,
    playlist_id: str,
    library_dir: Path,
    playlists_dir: Path,
) -> UnimportReport:
    connection.execute(
        "UPDATE spotify_playlist_catalog SET tracked = 0 WHERE spotify_id = ?",
        (playlist_id,),
    )
    connection.execute("DELETE FROM playlists WHERE spotify_id = ?", (playlist_id,))
    cleanup = clean_unreferenced_tracks(connection, library_dir, apply=True)
    playlists = build_playlists(connection, playlists_dir, allow_partial=True)
    return UnimportReport(cleanup, playlists)


def tracked_playlist_ids(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        row["spotify_id"]
        for row in connection.execute(
            "SELECT spotify_id FROM spotify_playlist_catalog WHERE tracked = 1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
    )


def update_tracked_playlists(
    connection: sqlite3.Connection,
    client: SpotifyClient,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> int:
    catalog = {entry.spotify_id: entry for entry in list_playlist_catalog(connection)}
    ids = tracked_playlist_ids(connection)
    for index, playlist_id in enumerate(ids, start=1):
        _check_cancel(cancelled)
        playlist = catalog.get(playlist_id)
        if playlist is None:
            continue
        _notify(progress, f"Checking {playlist.name} ({index}/{len(ids)})…")
        try:
            entries = fetch_one_playlist(
                client,
                playlist,
                cancelled=cancelled,
                progress=progress,
            )
        except SpotifyAPIError as exc:
            if exc.status_code == 403:
                continue
            raise
        refresh_playlist_items(connection, playlist_id, entries)
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('spotify_tracked_fetched_at', ?)",
        (_now(),),
    )
    _notify(progress, f"Checked {len(ids)} tracked playlist(s).")
    return len(ids)
