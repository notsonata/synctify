from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from ..gc import CleanupReport, clean_unreferenced_tracks
from ..playlists import PlaylistBuildReport, build_playlists
from .selection import apply_playlist_choices


@dataclass(slots=True, frozen=True)
class PlaylistCleanupReport:
    cleanup: CleanupReport
    playlists: PlaylistBuildReport


def _active_track_ids(
    connection: sqlite3.Connection,
    playlist_id: str,
) -> tuple[str, ...]:
    return tuple(
        str(row["track_id"])
        for row in connection.execute(
            "SELECT DISTINCT track_id FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
    )


def apply_reviewed_playlist(
    connection: sqlite3.Connection,
    playlist_id: str,
    library_dir: Path,
    playlists_dir: Path,
) -> PlaylistCleanupReport:
    """Apply reviewed choices and clean only tracks removed from this playlist."""
    before = set(_active_track_ids(connection, playlist_id))
    apply_playlist_choices(connection, playlist_id)
    after = set(_active_track_ids(connection, playlist_id))
    cleanup = clean_unreferenced_tracks(
        connection,
        library_dir,
        apply=True,
        spotify_ids=before - after,
    )
    playlists = build_playlists(connection, playlists_dir, allow_partial=True)
    return PlaylistCleanupReport(cleanup, playlists)


def unimport_reviewed_playlist(
    connection: sqlite3.Connection,
    playlist_id: str,
    library_dir: Path,
    playlists_dir: Path,
) -> PlaylistCleanupReport:
    """Unimport one playlist and clean only its now-unreferenced local tracks."""
    affected = _active_track_ids(connection, playlist_id)
    row = connection.execute(
        "SELECT tracked FROM spotify_playlist_catalog WHERE spotify_id = ?",
        (playlist_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown Spotify playlist: {playlist_id}")
    if not row["tracked"]:
        raise ValueError("playlist is not imported into Synctify")

    connection.execute(
        "UPDATE spotify_playlist_catalog SET tracked = 0 WHERE spotify_id = ?",
        (playlist_id,),
    )
    connection.execute("DELETE FROM playlists WHERE spotify_id = ?", (playlist_id,))
    cleanup = clean_unreferenced_tracks(
        connection,
        library_dir,
        apply=True,
        spotify_ids=affected,
    )
    playlists = build_playlists(connection, playlists_dir, allow_partial=True)
    return PlaylistCleanupReport(cleanup, playlists)
