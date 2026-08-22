from __future__ import annotations

from pathlib import Path

from synctify.config import Settings
from synctify.db import connect, initialize
from synctify.tui_backend import read_dashboard


def _settings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    home.mkdir()
    library.mkdir()
    playlists.mkdir()
    return Settings(
        home=home,
        library_dir=library,
        playlists_dir=playlists,
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def test_dashboard_counts_only_tracks_referenced_by_imported_playlists(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO playlists(spotify_id, name) VALUES ('playlist-1', 'Selected')"
        )
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, local_path, status) VALUES ('local', 'Local', 'Artist', '/tmp/local.flac', 'local')"
        )
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, status) VALUES ('missing', 'Missing', 'Artist', 'unresolved')"
        )
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, status) VALUES ('legacy', 'Legacy', 'Artist', 'unresolved')"
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', 'local', 0)"
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', 'missing', 1)"
        )

    state = read_dashboard(settings)

    assert state.tracks == 2
    assert state.local_tracks == 1
    assert state.unresolved == 1
    assert state.playlists == 1


def test_dashboard_resolution_count_excludes_legacy_unreferenced_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    initialize(settings.database_path)
    with connect(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO playlists(spotify_id, name) VALUES ('playlist-1', 'Selected')"
        )
        for spotify_id in ("active", "legacy"):
            connection.execute(
                "INSERT INTO tracks(spotify_id, title, artist, status) VALUES (?, 'Song', 'Artist', 'resolved')",
                (spotify_id,),
            )
            connection.execute(
                """
                INSERT INTO track_resolutions(
                    spotify_id, provider, provider_track_id, match_method, confidence,
                    is_manual, updated_at
                ) VALUES (?, 'qobuz', ?, 'isrc', 1.0, 0, '2026-08-23T00:00:00+00:00')
                """,
                (spotify_id, f"q-{spotify_id}"),
            )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', 'active', 0)"
        )

    state = read_dashboard(settings)

    assert state.tracks == 1
    assert state.resolutions == 1
    assert state.pending_downloads == 1
