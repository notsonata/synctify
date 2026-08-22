from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from .ingest import SpotifySnapshot


@dataclass(slots=True, frozen=True)
class ChangePlan:
    playlists_added: tuple[str, ...]
    playlists_removed: tuple[str, ...]
    playlists_renamed: tuple[tuple[str, str], ...]
    playlists_reordered: tuple[str, ...]
    tracks_added: int
    tracks_removed: int
    skipped_items: int
    inaccessible_playlists: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(
            self.playlists_added
            or self.playlists_removed
            or self.playlists_renamed
            or self.playlists_reordered
            or self.tracks_added
            or self.tracks_removed
        )


def _existing_playlists(connection: sqlite3.Connection) -> dict[str, tuple[str, list[str]]]:
    rows = connection.execute(
        "SELECT spotify_id, name FROM playlists WHERE source_kind IN ('liked', 'playlist')"
    ).fetchall()
    result = {}
    for row in rows:
        tracks = connection.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (row["spotify_id"],),
        ).fetchall()
        result[row["spotify_id"]] = (
            row["name"],
            [track["track_id"] for track in tracks],
        )
    return result


def plan_snapshot(connection: sqlite3.Connection, snapshot: SpotifySnapshot) -> ChangePlan:
    current = _existing_playlists(connection)
    desired = {
        playlist.spotify_id: (
            playlist.name,
            [entry.track.spotify_id for entry in playlist.tracks],
        )
        for playlist in snapshot.playlists
    }
    inaccessible_ids = set(snapshot.inaccessible_playlists)
    added_ids = desired.keys() - current.keys()
    removed_ids = current.keys() - desired.keys() - inaccessible_ids
    common_ids = current.keys() & desired.keys()
    renamed: list[tuple[str, str]] = []
    reordered: list[str] = []
    tracks_added = sum(len(desired[playlist_id][1]) for playlist_id in added_ids)
    tracks_removed = sum(len(current[playlist_id][1]) for playlist_id in removed_ids)
    for playlist_id in common_ids:
        old_name, old_tracks = current[playlist_id]
        new_name, new_tracks = desired[playlist_id]
        if old_name != new_name:
            renamed.append((old_name, new_name))
        old_counter = Counter(old_tracks)
        new_counter = Counter(new_tracks)
        tracks_added += sum((new_counter - old_counter).values())
        tracks_removed += sum((old_counter - new_counter).values())
        if old_tracks != new_tracks and old_counter == new_counter:
            reordered.append(new_name)
    return ChangePlan(
        tuple(sorted(desired[i][0] for i in added_ids)),
        tuple(sorted(current[i][0] for i in removed_ids)),
        tuple(sorted(renamed)),
        tuple(sorted(reordered)),
        tracks_added,
        tracks_removed,
        snapshot.skipped_items,
        snapshot.inaccessible_playlists,
    )


def apply_snapshot(connection: sqlite3.Connection, snapshot: SpotifySnapshot) -> None:
    """Apply Spotify desired state without committing the caller's transaction."""
    now = datetime.now(timezone.utc).isoformat()
    desired_ids = {playlist.spotify_id for playlist in snapshot.playlists}
    preserved_ids = set(snapshot.inaccessible_playlists)
    existing_ids = {
        row["spotify_id"]
        for row in connection.execute(
            "SELECT spotify_id FROM playlists WHERE source_kind IN ('liked', 'playlist')"
        )
    }
    for playlist_id in existing_ids - desired_ids - preserved_ids:
        connection.execute("DELETE FROM playlists WHERE spotify_id = ?", (playlist_id,))

    for playlist in snapshot.playlists:
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
                playlist.spotify_id,
                playlist.name,
                playlist.snapshot_id,
                now,
                playlist.source_kind,
                playlist.owner_id,
                int(playlist.collaborative),
            ),
        )
        connection.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist.spotify_id,),
        )
        for position, entry in enumerate(playlist.tracks):
            track = entry.track
            connection.execute(
                """
                INSERT INTO tracks(
                    spotify_id, isrc, title, artist, album, duration_ms, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'unresolved')
                ON CONFLICT(spotify_id) DO UPDATE SET
                    isrc=COALESCE(excluded.isrc, tracks.isrc),
                    title=excluded.title,
                    artist=excluded.artist,
                    album=excluded.album,
                    duration_ms=excluded.duration_ms
                """,
                (
                    track.spotify_id,
                    track.isrc,
                    track.title,
                    track.artist,
                    track.album,
                    track.duration_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO playlist_tracks(playlist_id, track_id, position, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (playlist.spotify_id, track.spotify_id, position, entry.added_at),
            )

    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('spotify_user_id', ?)",
        (snapshot.user_id,),
    )
    connection.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('spotify_last_pull_at', ?)",
        (now,),
    )


def format_plan(plan: ChangePlan) -> str:
    lines = [
        "Spotify changes",
        f"  Tracks added:     {plan.tracks_added}",
        f"  Tracks removed:   {plan.tracks_removed}",
        f"  Playlists added:  {len(plan.playlists_added)}",
        f"  Playlists removed:{len(plan.playlists_removed):>3}",
        f"  Playlists renamed:{len(plan.playlists_renamed):>3}",
        f"  Reordered:        {len(plan.playlists_reordered)}",
    ]
    if plan.skipped_items:
        lines.append(f"  Skipped non-track/local items: {plan.skipped_items}")
    if plan.inaccessible_playlists:
        lines.append("  Inaccessible playlist IDs preserved: " + ", ".join(plan.inaccessible_playlists))
    return "\n".join(lines)
