from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys

from .acquisition import pending_acquisitions
from .audit import LibraryAuditReport, audit_library
from .config import Settings
from .db import connect, initialize
from .doctor import DoctorReport, run_doctor
from .gc import clean_unreferenced_tracks
from .playlists import build_playlists
from .resolution import set_manual_override
from .spotify.auth import SpotifyAuth, SpotifyOAuthConfig
from .spotify.client import SpotifyClient
from .spotify.selection import (
    PlaylistCatalogEntry,
    PlaylistItemState,
    SpotifySelectionCancelled,
    apply_playlist_choices,
    confirm_playlist,
    fetch_one_playlist,
    fetch_playlist_catalog,
    list_playlist_catalog,
    list_playlist_items,
    refresh_playlist_items,
    set_item_choice,
    store_playlist_catalog,
    unimport_playlist,
    update_tracked_playlists,
)
from .user_config import effective_user_config, load_user_config

CancelCheck = Callable[[], bool]
ProgressReporter = Callable[[str], None]


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


@dataclass(slots=True, frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    cancelled: bool = False


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
        last_pull_row = None
        if "metadata" in tables:
            for key in (
                "spotify_tracked_fetched_at",
                "spotify_catalog_fetched_at",
                "spotify_last_pull_at",
            ):
                last_pull_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?",
                    (key,),
                ).fetchone()
                if last_pull_row is not None:
                    break

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


def read_spotify_playlists(settings: Settings) -> tuple[PlaylistCatalogEntry, ...]:
    if not settings.database_path.exists() or not settings.database_path.is_file():
        return ()
    with _open_readonly(settings.database_path) as connection:
        if "spotify_playlist_catalog" not in _tables(connection):
            return ()
        return list_playlist_catalog(connection)


def read_spotify_playlist_items(
    settings: Settings,
    playlist_id: str,
) -> tuple[PlaylistItemState, ...]:
    if not settings.database_path.exists() or not settings.database_path.is_file():
        return ()
    with _open_readonly(settings.database_path) as connection:
        if "spotify_playlist_items" not in _tables(connection):
            return ()
        return list_playlist_items(connection, playlist_id)


def _spotify_client(settings: Settings) -> SpotifyClient:
    config = SpotifyOAuthConfig.load(settings.spotify_config_path)
    return SpotifyClient(SpotifyAuth(config))


def fetch_spotify_playlists(
    settings: Settings,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> int:
    settings.ensure_directories()
    initialize(settings.database_path)
    with _spotify_client(settings) as client:
        user_id, entries = fetch_playlist_catalog(
            client,
            cancelled=cancelled,
            progress=progress,
        )
    if cancelled is not None and cancelled():
        raise SpotifySelectionCancelled("Spotify operation cancelled")
    with connect(settings.database_path) as connection:
        store_playlist_catalog(connection, user_id, entries)
    return len(entries)


def fetch_spotify_playlist_items(
    settings: Settings,
    playlist_id: str,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> int:
    settings.ensure_directories()
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        catalog = {item.spotify_id: item for item in list_playlist_catalog(connection)}
        playlist = catalog.get(playlist_id)
    if playlist is None:
        raise KeyError(f"unknown Spotify playlist: {playlist_id}")
    with _spotify_client(settings) as client:
        entries = fetch_one_playlist(
            client,
            playlist,
            cancelled=cancelled,
            progress=progress,
        )
    if cancelled is not None and cancelled():
        raise SpotifySelectionCancelled("Spotify operation cancelled")
    with connect(settings.database_path) as connection:
        refresh_playlist_items(connection, playlist_id, entries)
    return len(entries)


def fetch_tracked_spotify_updates(
    settings: Settings,
    *,
    cancelled: CancelCheck | None = None,
    progress: ProgressReporter | None = None,
) -> int:
    settings.ensure_directories()
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        with _spotify_client(settings) as client:
            return update_tracked_playlists(
                connection,
                client,
                cancelled=cancelled,
                progress=progress,
            )


def choose_spotify_item(
    settings: Settings,
    playlist_id: str,
    item_key: str,
    included: bool,
) -> None:
    with connect(settings.database_path) as connection:
        set_item_choice(
            connection,
            playlist_id,
            item_key,
            "included" if included else "excluded",
        )


def confirm_spotify_playlist(settings: Settings, playlist_id: str) -> None:
    with connect(settings.database_path) as connection:
        confirm_playlist(connection, playlist_id)


def apply_spotify_playlist(
    settings: Settings,
    playlist_id: str,
) -> None:
    with connect(settings.database_path) as connection:
        apply_playlist_choices(connection, playlist_id)
        clean_unreferenced_tracks(connection, settings.library_dir, apply=True)
        build_playlists(connection, settings.playlists_dir, allow_partial=True)


def unimport_spotify_playlist(settings: Settings, playlist_id: str) -> None:
    with connect(settings.database_path) as connection:
        unimport_playlist(
            connection,
            playlist_id,
            settings.library_dir,
            settings.playlists_dir,
        )


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_cli_command(
    settings: Settings,
    args: Sequence[str],
    *,
    on_output: Callable[[str], None] | None = None,
    cancelled: CancelCheck | None = None,
) -> CommandResult:
    """Run a Synctify CLI command with streamed output and cooperative cancellation."""
    normalized = tuple(str(part) for part in args)
    if not normalized:
        raise ValueError("command arguments are required")
    if normalized[0] == "tui":
        raise ValueError("cannot launch a nested Synctify TUI")

    environment = os.environ.copy()
    environment["SYNCTIFY_HOME"] = str(settings.home)
    environment["SYNCTIFY_LIBRARY_DIR"] = str(settings.library_dir)
    environment["SYNCTIFY_SKIP_AUTO_UPDATE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        [sys.executable, "-m", "synctify.app", *normalized],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=environment,
    )
    if process.stdout is None:
        _stop_process(process)
        raise RuntimeError("failed to capture Synctify command output")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    was_cancelled = False
    try:
        while True:
            if cancelled is not None and cancelled():
                was_cancelled = True
                _stop_process(process)
                break
            events = selector.select(timeout=0.1)
            for key, _mask in events:
                raw_line = key.fileobj.readline()
                if raw_line:
                    if on_output is not None:
                        on_output(raw_line.rstrip("\r\n"))
            if process.poll() is not None:
                for raw_line in process.stdout:
                    if on_output is not None:
                        on_output(raw_line.rstrip("\r\n"))
                break
    except BaseException:
        _stop_process(process)
        raise
    finally:
        selector.close()
        process.stdout.close()

    return CommandResult(
        args=normalized,
        returncode=process.wait(),
        cancelled=was_cancelled,
    )
