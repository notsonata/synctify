from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from synctify.cli_entry import app
from synctify.db import connect, initialize
from synctify.gc import clean_unreferenced_tracks, collectible_tracks
from synctify.resolution import set_manual_override


def _insert_track(connection, spotify_id: str, path: Path, *, title: str | None = None) -> None:
    connection.execute(
        """
        INSERT INTO tracks(spotify_id, title, artist, album, duration_ms, local_path, sha256, status)
        VALUES (?, ?, 'Artist', 'Album', 200000, ?, 'hash', 'local')
        """,
        (spotify_id, title or spotify_id, str(path)),
    )


def _reference_track(connection, playlist_id: str, spotify_id: str, position: int = 0) -> None:
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
        (playlist_id, playlist_id),
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, ?)",
        (playlist_id, spotify_id, position),
    )


def test_referenced_track_is_not_collectible(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    track = library / "Artist" / "Album" / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"flac")
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", track)
        _reference_track(connection, "playlist-1", "spotify-1")

        assert collectible_tracks(connection, library) == ()


def test_preview_does_not_delete_or_mutate_state(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    track = library / "Artist" / "Album" / "Orphan.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"123456")
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", track)
        report = clean_unreferenced_tracks(connection, library)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert report.dry_run is True
    assert len(report.candidates) == 1
    assert report.reclaimable_bytes == 6
    assert track.exists()
    assert row["local_path"] == str(track)
    assert row["sha256"] == "hash"
    assert row["status"] == "local"


def test_apply_deletes_unreferenced_file_but_preserves_resolution(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    track = library / "Artist" / "Album" / "Orphan.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"flac")
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", track)
        set_manual_override(connection, "spotify-1", "qobuz", "q-123")

        report = clean_unreferenced_tracks(connection, library, apply=True)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()
        resolution = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert len(report.deleted) == 1
    assert not track.exists()
    assert not track.parent.exists()
    assert row["local_path"] is None
    assert row["sha256"] is None
    assert row["status"] == "resolved"
    assert resolution["provider"] == "qobuz"
    assert resolution["provider_track_id"] == "q-123"


def test_apply_clears_stale_missing_local_state(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    missing = library / "Artist" / "Album" / "Missing.flac"
    library.mkdir()
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", missing)
        report = clean_unreferenced_tracks(connection, library, apply=True)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert len(report.cleared_missing) == 1
    assert row["local_path"] is None
    assert row["sha256"] is None
    assert row["status"] == "unresolved"


def test_path_outside_canonical_library_is_never_deleted(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    library.mkdir()
    external = tmp_path / "external.flac"
    external.write_bytes(b"external")
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", external)
        report = clean_unreferenced_tracks(connection, library, apply=True)
        row = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert external.exists()
    assert len(report.skipped) == 1
    assert "outside the canonical library" in (report.skipped[0].reason or "")
    assert row["local_path"] == str(external)


def test_symlink_escape_is_never_deleted(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    library.mkdir()
    external = tmp_path / "external.flac"
    external.write_bytes(b"external")
    link = library / "linked.flac"
    link.symlink_to(external)
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-1", link)
        report = clean_unreferenced_tracks(connection, library, apply=True)

    assert external.exists()
    assert link.exists()
    assert len(report.skipped) == 1
    assert "outside the canonical library" in (report.skipped[0].reason or "")


def test_shared_local_path_is_protected(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    track = library / "Artist" / "Album" / "Shared.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"shared")
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, "spotify-orphan", track)
        _insert_track(connection, "spotify-used", track)
        _reference_track(connection, "playlist-1", "spotify-used")

        report = clean_unreferenced_tracks(connection, library, apply=True)

    assert track.exists()
    assert len(report.candidates) == 1
    assert len(report.skipped) == 1
    assert "shared by multiple track records" in (report.skipped[0].reason or "")


def test_clean_cli_is_preview_by_default_and_requires_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    library = home / "library"
    track = library / "Artist" / "Album" / "CLI.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"cli")
    database = home / "synctify.sqlite3"
    initialize(database)
    with connect(database) as connection:
        _insert_track(connection, "spotify-cli", track, title="CLI")

    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    runner = CliRunner()

    preview = runner.invoke(app, ["clean"])
    assert preview.exit_code == 0
    assert "Preview only" in preview.stdout
    assert track.exists()

    applied = runner.invoke(app, ["clean", "--apply"])
    assert applied.exit_code == 0
    assert "Deleted: 1" in applied.stdout
    assert not track.exists()
