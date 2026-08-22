from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .acquisition import pending_acquisitions
from .audit import LibraryAuditReport, audit_library
from .config import Settings
from .db import connect
from .doctor import DoctorReport, run_doctor
from .resolution import set_manual_override
from .user_config import effective_user_config, load_user_config


@dataclass(slots=True, frozen=True)
class DashboardState:
    initialized: bool
    tracks: int
    local_tracks: int
    unresolved: int
    resolutions: int
    pending_downloads: int
    playlists: int
    targets: int
    last_pull: str | None
    library_dir: Path


@dataclass(slots=True, frozen=True)
class UnresolvedTrack:
    spotify_id: str
    title: str
    artist: str
    album: str | None
    isrc: str | None


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def read_dashboard(settings: Settings) -> DashboardState:
    """Read dashboard state without creating or migrating Synctify state."""
    if not settings.database_path.exists() or not settings.database_path.is_file():
        return DashboardState(
            initialized=False,
            tracks=0,
            local_tracks=0,
            unresolved=0,
            resolutions=0,
            pending_downloads=0,
            playlists=0,
            targets=0,
            last_pull=None,
            library_dir=settings.library_dir,
        )

    with _open_readonly(settings.database_path) as connection:
        tables = _tables(connection)
        required = {"tracks", "playlists", "playlist_tracks"}
        if not required.issubset(tables):
            raise sqlite3.DatabaseError(
                "database is missing required table(s): "
                + ", ".join(sorted(required - tables))
            )

        tracks = int(connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
        local_tracks = int(
            connection.execute(
                "SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL AND local_path != ''"
            ).fetchone()[0]
        )
        unresolved = int(
            connection.execute(
                "SELECT COUNT(*) FROM tracks WHERE status = 'unresolved'"
            ).fetchone()[0]
        )
        playlists = int(connection.execute("SELECT COUNT(*) FROM playlists").fetchone()[0])
        resolutions = (
            int(connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0])
            if "track_resolutions" in tables
            else 0
        )
        pending = (
            len(pending_acquisitions(connection))
            if "track_resolutions" in tables
            else 0
        )
        targets = (
            int(connection.execute("SELECT COUNT(*) FROM sync_targets").fetchone()[0])
            if "sync_targets" in tables
            else 0
        )
        last_pull_row = (
            connection.execute(
                "SELECT value FROM metadata WHERE key = 'spotify_last_pull_at'"
            ).fetchone()
            if "metadata" in tables
            else None
        )

    return DashboardState(
        initialized=True,
        tracks=tracks,
        local_tracks=local_tracks,
        unresolved=unresolved,
        resolutions=resolutions,
        pending_downloads=pending,
        playlists=playlists,
        targets=targets,
        last_pull=str(last_pull_row[0]) if last_pull_row is not None else None,
        library_dir=settings.library_dir,
    )


def list_unresolved_tracks(
    settings: Settings,
    *,
    limit: int = 500,
) -> tuple[UnresolvedTrack, ...]:
    """Return desired tracks that do not currently have a source resolution."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if not settings.database_path.exists() or not settings.database_path.is_file():
        return ()

    with _open_readonly(settings.database_path) as connection:
        tables = _tables(connection)
        if not {"tracks", "playlist_tracks"}.issubset(tables):
            return ()
        resolution_filter = (
            "AND NOT EXISTS (SELECT 1 FROM track_resolutions AS r WHERE r.spotify_id = t.spotify_id)"
            if "track_resolutions" in tables
            else ""
        )
        rows = connection.execute(
            f"""
            SELECT t.spotify_id, t.title, t.artist, t.album, t.isrc
            FROM tracks AS t
            WHERE EXISTS (
                SELECT 1
                FROM playlist_tracks AS pt
                WHERE pt.track_id = t.spotify_id
            )
            {resolution_filter}
            ORDER BY t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.title COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return tuple(
        UnresolvedTrack(
            spotify_id=str(row["spotify_id"]),
            title=str(row["title"]),
            artist=str(row["artist"]),
            album=str(row["album"]) if row["album"] is not None else None,
            isrc=str(row["isrc"]) if row["isrc"] is not None else None,
        )
        for row in rows
    )


def set_manual_resolution(
    settings: Settings,
    spotify_id: str,
    provider: str,
    provider_track_id: str,
) -> None:
    """Persist a manual resolution through the same core resolver used by the CLI."""
    if not settings.database_path.exists() or not settings.database_path.is_file():
        raise RuntimeError("Synctify is not initialized")
    with connect(settings.database_path) as connection:
        set_manual_override(connection, spotify_id, provider, provider_track_id)


def read_doctor_report(settings: Settings) -> DoctorReport:
    """Run the existing read-only doctor using effective executable configuration."""
    config = effective_user_config(load_user_config(settings.home))
    return run_doctor(
        settings,
        qobuz_dl=config.qobuz_dl,
        streamrip=config.streamrip,
        rclone=config.rclone,
    )


def read_audit_report(settings: Settings) -> LibraryAuditReport:
    """Run a read-only library audit for display in the TUI."""
    if not settings.database_path.exists() or not settings.database_path.is_file():
        raise RuntimeError("Synctify is not initialized")
    with _open_readonly(settings.database_path) as connection:
        return audit_library(connection, settings.library_dir, repair=False)
