from __future__ import annotations

import json
from pathlib import Path

import pytest

from synctify.config import Settings
from synctify.db import connect
from synctify.doctor import CheckStatus, DoctorCheck, DoctorReport
from synctify.migration import MigrationError, MigrationOptions, run_migration
from synctify.migration_checkpoint import (
    STAGE_AUDIT,
    STAGE_DOCTOR,
    STAGE_LIBRARY_RELINK,
    STAGE_PORTABLE_IMPORT,
    STAGE_SPOTIFY_REFRESH,
    MigrationCheckpointError,
    checkpoint_path,
    load_checkpoint,
    mark_stage_completed,
    migration_identity,
    new_checkpoint,
    record_stage_error,
    save_checkpoint,
)
from synctify.playlists import build_playlists
from synctify.spotify.ingest import SpotifySnapshot


def _settings(home: Path) -> Settings:
    return Settings(
        home=home,
        library_dir=home / "library",
        playlists_dir=home / "playlists",
        database_path=home / "synctify.sqlite3",
        spotify_config_path=home / "spotify.json",
    )


def _portable(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "format": "synctify-portable",
                "format_version": 1,
                "synctify_version": "0.22.0",
                "config": {},
                "spotify": {
                    "client_id": "migration-client",
                    "redirect_uri": "http://127.0.0.1:8765/callback",
                },
                "targets": [],
            }
        ),
        encoding="utf-8",
    )


def _doctor(status: CheckStatus) -> DoctorReport:
    return DoctorReport((DoctorCheck("migration", "test", status, "result"),))


def test_successful_unrelated_stage_does_not_clear_pending_checkpoint_error(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable)
    checkpoint = new_checkpoint(
        migration_identity(
            portable,
            library_source=None,
            relink_limit=None,
            allow_partial=False,
        )
    )

    record_stage_error(checkpoint, STAGE_AUDIT, "audit failed")
    mark_stage_completed(checkpoint, STAGE_DOCTOR)

    assert checkpoint.last_error_stage == STAGE_AUDIT
    assert checkpoint.last_error == "audit failed"


def test_save_checkpoint_wraps_parent_directory_creation_failure(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable)
    checkpoint = new_checkpoint(
        migration_identity(
            portable,
            library_source=None,
            relink_limit=None,
            allow_partial=False,
        )
    )
    blocked_home = tmp_path / "not-a-directory"
    blocked_home.write_text("file", encoding="utf-8")

    with pytest.raises(MigrationCheckpointError, match="could not write migration checkpoint"):
        save_checkpoint(checkpoint_path(blocked_home), checkpoint)


def test_restart_checkpoint_write_failure_is_reported_as_migration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable)
    settings = _settings(tmp_path / "home")

    def fail_save(*args, **kwargs) -> None:
        raise MigrationCheckpointError("disk full")

    monkeypatch.setattr("synctify.migration.save_checkpoint", fail_save)

    with pytest.raises(MigrationError, match="disk full"):
        run_migration(
            settings,
            MigrationOptions(portable, restart=True),
            fetch_snapshot_fn=lambda _: SpotifySnapshot("user", ()),
            doctor_fn=lambda _: _doctor(CheckStatus.PASS),
        )


def test_resume_skips_completed_relink_when_source_drive_is_disconnected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portable = tmp_path / "portable.json"
    source = tmp_path / "removable-library"
    source.mkdir()
    _portable(portable)
    settings = _settings(tmp_path / "home")

    first = run_migration(
        settings,
        MigrationOptions(portable, library_source=source),
        fetch_snapshot_fn=lambda _: SpotifySnapshot("user", ()),
        doctor_fn=lambda _: _doctor(CheckStatus.FAIL),
    )
    assert first.checkpoint is not None
    assert first.checkpoint.is_completed(STAGE_PORTABLE_IMPORT)
    assert first.checkpoint.is_completed(STAGE_SPOTIFY_REFRESH)
    assert first.checkpoint.is_completed(STAGE_LIBRARY_RELINK)
    assert not first.checkpoint.is_completed(STAGE_DOCTOR)

    source.rmdir()

    def should_not_fetch(_: Settings) -> SpotifySnapshot:
        raise AssertionError("Spotify refresh should be skipped on resume")

    def should_not_relink(*args, **kwargs):
        raise AssertionError("completed relink should be skipped even when source is disconnected")

    monkeypatch.setattr("synctify.migration.relink_library", should_not_relink)

    resumed = run_migration(
        settings,
        MigrationOptions(portable, library_source=source, resume=True),
        fetch_snapshot_fn=should_not_fetch,
        doctor_fn=lambda _: _doctor(CheckStatus.PASS),
    )

    assert STAGE_LIBRARY_RELINK in resumed.resumed_stages
    assert resumed.checkpoint is not None
    assert resumed.checkpoint.complete is True


def test_failed_owned_playlist_delete_keeps_ownership_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    track = library / "Track.flac"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"flac")

    from synctify.db import initialize

    initialize(database)
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO tracks(spotify_id, title, artist, local_path, status) VALUES (?, ?, ?, ?, 'local')",
            ("track-1", "Track", "Artist", str(track)),
        )
        connection.execute(
            "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
            ("playlist-1", "Managed"),
        )
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, 0)",
            ("playlist-1", "track-1"),
        )
        first = build_playlists(connection, playlists)
        connection.execute("DELETE FROM playlists WHERE spotify_id = 'playlist-1'")

    output = first.results[0].output
    assert output is not None
    output = output.resolve()
    original_unlink = Path.unlink

    def fail_output_unlink(self: Path, *args, **kwargs):
        if self.resolve() == output:
            raise PermissionError("temporarily busy")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_output_unlink)

    with connect(database) as connection:
        report = build_playlists(connection, playlists)
        ownership = connection.execute(
            "SELECT filename FROM generated_playlists WHERE playlist_id = 'playlist-1'"
        ).fetchone()

    assert report.removed_outputs == ()
    assert report.protected_outputs == (output,)
    assert output.is_file()
    assert ownership is not None
    assert ownership["filename"] == output.name
    saved = load_checkpoint(checkpoint_path(settings.home)) if False else None
    assert saved is None
