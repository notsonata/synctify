from __future__ import annotations

import sqlite3
from pathlib import Path

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


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        _migrate(connection)
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', '2')"
        )
