from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from synctify.config import Settings
from synctify.db import connect
from synctify.doctor import CheckStatus, DoctorCheck, DoctorReport
from synctify.migration import (
    MigrationOptions,
    preview_migration,
    run_migration,
)
from synctify.migration_cli import app
from synctify.spotify.ingest import PlaylistEntry, SpotifyPlaylist, SpotifySnapshot, SpotifyTrack


runner = CliRunner()


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
                "synctify_version": "0.20.0",
                "config": {"source_priority": ["qobuz", "tidal"]},
                "spotify": {
                    "client_id": "migration-client",
                    "redirect_uri": "http://127.0.0.1:8765/callback",
                },
                "targets": [],
            }
        ),
        encoding="utf-8",
    )


def _snapshot() -> SpotifySnapshot:
    track = SpotifyTrack(
        spotify_id="spotify-track-1",
        title="Paranoid Android",
        artist="Radiohead",
        album="OK Computer",
        duration_ms=386_000,
        isrc="GBAYE9701376",
    )
    playlist = SpotifyPlaylist(
        spotify_id="spotify-playlist-1",
        name="Migration Test",
        tracks=(PlaylistEntry(track),),
        snapshot_id="snapshot-1",
        source_kind="playlist",
        owner_id="user-1",
    )
    return SpotifySnapshot("user-1", (playlist,))


def _doctor(_: Settings) -> DoctorReport:
    return DoctorReport(
        (
            DoctorCheck("migration", "test", CheckStatus.PASS, "ok"),
        )
    )


def _metadata_block(block_type: int, payload: bytes, *, last: bool) -> bytes:
    first = block_type | (0x80 if last else 0)
    return bytes([first]) + len(payload).to_bytes(3, "big") + payload


def _streaminfo(duration_ms: int, sample_rate: int = 44_100) -> bytes:
    total_samples = round(duration_ms * sample_rate / 1000)
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    return b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16


def _vorbis_comments(tags: dict[str, str]) -> bytes:
    vendor = b"synctify-migration-test"
    comments = [f"{key.upper()}={value}".encode("utf-8") for key, value in tags.items()]
    payload = len(vendor).to_bytes(4, "little") + vendor
    payload += len(comments).to_bytes(4, "little")
    for comment in comments:
        payload += len(comment).to_bytes(4, "little") + comment
    return payload


def _write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"fLaC"
    data += _metadata_block(0, _streaminfo(386_000), last=False)
    data += _metadata_block(
        4,
        _vorbis_comments(
            {
                "title": "Paranoid Android",
                "artist": "Radiohead",
                "album": "OK Computer",
                "isrc": "GBAYE9701376",
            }
        ),
        last=True,
    )
    path.write_bytes(data)


def test_preview_is_side_effect_free(tmp_path: Path) -> None:
    home = tmp_path / "new-mac"
    portable = tmp_path / "portable.json"
    _portable(portable)
    settings = _settings(home)

    report = preview_migration(settings, MigrationOptions(portable))

    assert report.applied is False
    assert report.import_plan.spotify_action == "create"
    assert report.import_plan.config_keys == ("source_priority",)
    assert not home.exists()


def test_apply_restores_desired_state_and_runs_final_checks(tmp_path: Path) -> None:
    home = tmp_path / "new-mac"
    portable = tmp_path / "portable.json"
    _portable(portable)
    settings = _settings(home)

    report = run_migration(
        settings,
        MigrationOptions(portable),
        fetch_snapshot_fn=lambda _: _snapshot(),
        doctor_fn=_doctor,
    )

    assert report.applied is True
    assert report.spotify_plan is not None
    assert report.spotify_plan.tracks_added == 1
    assert report.relink is None
    assert report.playlists is not None
    assert report.playlists.incomplete == 1
    assert report.audit is not None
    assert report.doctor is not None
    assert report.operational_failures == 0
    assert settings.spotify_config_path.exists()
    assert settings.library_dir.is_dir()
    with connect(settings.database_path) as connection:
        track = connection.execute(
            "SELECT local_path, status FROM tracks WHERE spotify_id = 'spotify-track-1'"
        ).fetchone()
        assert track is not None
        assert track["local_path"] is None
        assert track["status"] == "unresolved"


def test_apply_with_external_library_relinks_then_builds_playlist(tmp_path: Path) -> None:
    home = tmp_path / "new-mac"
    portable = tmp_path / "portable.json"
    source = tmp_path / "copied-library"
    source_flac = source / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    _portable(portable)
    _write_flac(source_flac)
    settings = _settings(home)

    report = run_migration(
        settings,
        MigrationOptions(portable, library_source=source),
        fetch_snapshot_fn=lambda _: _snapshot(),
        doctor_fn=_doctor,
    )

    assert report.relink is not None
    assert len(report.relink.matches) == 1
    assert report.relink.copied == 1
    assert not report.relink.failures
    assert source_flac.exists()
    copied = settings.library_dir / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    assert copied.is_file()
    assert report.playlists is not None
    assert report.playlists.written == 1
    assert report.playlists.incomplete == 0
    playlist = settings.playlists_dir / "Migration Test.m3u8"
    assert playlist.is_file()
    assert "#SYNCTIFY:playlist-id=spotify-playlist-1" in playlist.read_text(encoding="utf-8")
    assert report.audit is not None
    assert not report.audit.failures
    assert not report.audit.untracked_files


def test_spotify_login_runs_after_public_config_import_and_before_fetch(tmp_path: Path) -> None:
    home = tmp_path / "new-mac"
    portable = tmp_path / "portable.json"
    _portable(portable)
    settings = _settings(home)
    events: list[str] = []

    def login(current: Settings) -> None:
        assert current.spotify_config_path.exists()
        events.append("login")

    def fetch(_: Settings) -> SpotifySnapshot:
        events.append("fetch")
        return _snapshot()

    run_migration(
        settings,
        MigrationOptions(portable, spotify_login=True),
        spotify_login_fn=login,
        fetch_snapshot_fn=fetch,
        doctor_fn=_doctor,
    )

    assert events == ["login", "fetch"]


def test_cli_preview_lists_stages_without_creating_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "new-mac"
    portable = tmp_path / "portable.json"
    _portable(portable)
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = runner.invoke(app, ["migrate", str(portable)])

    assert result.exit_code == 0
    assert "Mode: preview" in result.stdout
    assert "Planned stages" in result.stdout
    assert "No local state was changed" in result.stdout
    assert not home.exists()
