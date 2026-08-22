from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "6"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    spotify_id TEXT PRIMARY KEY,
    isrc TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration_ms INTEGER,
    qobuz_id TEXT,
    local_path TEXT,
    sha256 TEXT,
    status TEXT NOT NULL DEFAULT 'unresolved'
);

CREATE TABLE IF NOT EXISTS playlists (
    spotify_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    snapshot_id TEXT,
    last_checked_at TEXT,
    source_kind TEXT NOT NULL DEFAULT 'playlist',
    owner_id TEXT,
    collaborative INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL REFERENCES playlists(spotify_id) ON DELETE CASCADE,
    track_id TEXT NOT NULL REFERENCES tracks(spotify_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at TEXT,
    PRIMARY KEY (playlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_playlist_tracks_track_id
ON playlist_tracks(track_id);

CREATE TABLE IF NOT EXISTS spotify_playlist_catalog (
    spotify_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    snapshot_id TEXT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('liked', 'playlist')),
    owner_id TEXT,
    collaborative INTEGER NOT NULL DEFAULT 0,
    track_count INTEGER,
    tracked INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT NOT NULL,
    items_fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_spotify_playlist_catalog_tracked
ON spotify_playlist_catalog(tracked, name);

CREATE TABLE IF NOT EXISTS spotify_playlist_items (
    playlist_id TEXT NOT NULL REFERENCES spotify_playlist_catalog(spotify_id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    track_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration_ms INTEGER,
    isrc TEXT,
    state TEXT NOT NULL CHECK (state IN ('included', 'excluded', 'pending_add', 'pending_remove')),
    present INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (playlist_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_spotify_playlist_items_state
ON spotify_playlist_items(playlist_id, state, position);

CREATE TABLE IF NOT EXISTS track_resolutions (
    spotify_id TEXT PRIMARY KEY REFERENCES tracks(spotify_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_track_id TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK (match_method IN ('manual', 'isrc', 'metadata')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    is_manual INTEGER NOT NULL DEFAULT 0,
    candidate_isrc TEXT,
    candidate_title TEXT,
    candidate_artist TEXT,
    candidate_album TEXT,
    candidate_duration_ms INTEGER,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_track_resolutions_provider
ON track_resolutions(provider, provider_track_id);

CREATE TABLE IF NOT EXISTS sync_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    destination TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('mirror', 'backup'))
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER REFERENCES sync_targets(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    added INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_backup_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES sync_targets(id) ON DELETE CASCADE,
    playlist_file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playlist_backup_snapshots_latest
ON playlist_backup_snapshots(target_id, playlist_file, id);

CREATE TABLE IF NOT EXISTS generated_playlists (
    playlist_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    updated_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate(connection: sqlite3.Connection) -> None:
    playlist_columns = _columns(connection, "playlists")
    if "source_kind" not in playlist_columns:
        connection.execute(
            "ALTER TABLE playlists ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'playlist'"
        )
    if "owner_id" not in playlist_columns:
        connection.execute("ALTER TABLE playlists ADD COLUMN owner_id TEXT")
    if "collaborative" not in playlist_columns:
        connection.execute(
            "ALTER TABLE playlists ADD COLUMN collaborative INTEGER NOT NULL DEFAULT 0"
        )

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS track_resolutions (
            spotify_id TEXT PRIMARY KEY REFERENCES tracks(spotify_id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_track_id TEXT NOT NULL,
            match_method TEXT NOT NULL CHECK (match_method IN ('manual', 'isrc', 'metadata')),
            confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            is_manual INTEGER NOT NULL DEFAULT 0,
            candidate_isrc TEXT,
            candidate_title TEXT,
            candidate_artist TEXT,
            candidate_album TEXT,
            candidate_duration_ms INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_track_resolutions_provider
        ON track_resolutions(provider, provider_track_id);

        CREATE TABLE IF NOT EXISTS playlist_backup_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL REFERENCES sync_targets(id) ON DELETE CASCADE,
            playlist_file TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_playlist_backup_snapshots_latest
        ON playlist_backup_snapshots(target_id, playlist_file, id);

        CREATE TABLE IF NOT EXISTS generated_playlists (
            playlist_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spotify_playlist_catalog (
            spotify_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            snapshot_id TEXT,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('liked', 'playlist')),
            owner_id TEXT,
            collaborative INTEGER NOT NULL DEFAULT 0,
            track_count INTEGER,
            tracked INTEGER NOT NULL DEFAULT 0,
            available INTEGER NOT NULL DEFAULT 1,
            fetched_at TEXT NOT NULL,
            items_fetched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_spotify_playlist_catalog_tracked
        ON spotify_playlist_catalog(tracked, name);

        CREATE TABLE IF NOT EXISTS spotify_playlist_items (
            playlist_id TEXT NOT NULL REFERENCES spotify_playlist_catalog(spotify_id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            track_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            added_at TEXT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT,
            duration_ms INTEGER,
            isrc TEXT,
            state TEXT NOT NULL CHECK (state IN ('included', 'excluded', 'pending_add', 'pending_remove')),
            present INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (playlist_id, item_key)
        );
        CREATE INDEX IF NOT EXISTS idx_spotify_playlist_items_state
        ON spotify_playlist_items(playlist_id, state, position);
        """
    )

    # Seed the catalog from older all-at-once Spotify imports so existing users can
    # manage those playlists with the new selection flow without re-importing data.
    now = "1970-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT OR IGNORE INTO spotify_playlist_catalog(
            spotify_id, name, snapshot_id, source_kind, owner_id, collaborative,
            track_count, tracked, available, fetched_at, items_fetched_at
        )
        SELECT
            p.spotify_id, p.name, p.snapshot_id, p.source_kind, p.owner_id,
            p.collaborative, COUNT(pt.position), 1, 1,
            COALESCE(p.last_checked_at, ?), p.last_checked_at
        FROM playlists AS p
        LEFT JOIN playlist_tracks AS pt ON pt.playlist_id = p.spotify_id
        WHERE p.source_kind IN ('liked', 'playlist')
        GROUP BY p.spotify_id
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO spotify_playlist_items(
            playlist_id, item_key, track_id, position, added_at, title, artist,
            album, duration_ms, isrc, state, present
        )
        SELECT
            pt.playlist_id,
            pt.track_id || ':' || COALESCE(pt.added_at, printf('%08d', pt.position)),
            pt.track_id,
            pt.position,
            pt.added_at,
            t.title,
            t.artist,
            t.album,
            t.duration_ms,
            t.isrc,
            'included',
            1
        FROM playlist_tracks AS pt
        JOIN tracks AS t ON t.spotify_id = pt.track_id
        JOIN spotify_playlist_catalog AS c ON c.spotify_id = pt.playlist_id
        """
    )


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        _migrate(connection)
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
