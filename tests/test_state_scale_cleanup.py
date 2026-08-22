from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from synctify.acquisition import AcquisitionTask, acquire_tasks
from synctify.db import connect, initialize
from synctify.migration_cli import app
from synctify.providers.base import AcquiredTrack
from synctify.providers.reconcile import find_existing_flac
from synctify.resolution import (
    Candidate,
    MatchMethod,
    Resolution,
    ResolutionStatus,
    save_resolution,
    set_manual_override,
)
from synctify.spotify.ingest import PlaylistEntry, SpotifyPlaylist, SpotifySnapshot, SpotifyTrack
from synctify.spotify.state import apply_snapshot
from synctify.workflow import run_update_workflow


class _Search:
    name = "fake-search"
    supported_sources = frozenset({"qobuz", "tidal", "deezer"})

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def search(self, track, source: str, *, limit: int | None = None):
        return [
            Candidate(
                source,
                f"{source}-replacement",
                track.title,
                track.artist,
                track.album,
                track.isrc,
                track.duration_ms,
            )
        ]


class _Downloader:
    name = "fake-downloader"
    supported_sources = frozenset({"qobuz", "tidal", "deezer"})

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        path = destination / f"{candidate.provider_track_id}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return AcquiredTrack(candidate.provider, candidate.provider_track_id, path)


def _snapshot() -> SpotifySnapshot:
    track = SpotifyTrack(
        spotify_id="spotify-1",
        title="Song",
        artist="Artist",
        album="Album",
        duration_ms=180_000,
        isrc="USAAA2600001",
    )
    return SpotifySnapshot(
        "user-1",
        (
            SpotifyPlaylist(
                spotify_id="playlist-1",
                name="Playlist",
                tracks=(PlaylistEntry(track),),
                snapshot_id="snapshot-1",
                source_kind="playlist",
                owner_id="user-1",
            ),
        ),
    )


def test_update_retires_legacy_soundcloud_resolution_and_falls_back(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    snapshot = _snapshot()

    with connect(database) as connection:
        apply_snapshot(connection, snapshot)
        connection.execute(
            """
            INSERT INTO track_resolutions(
                spotify_id, provider, provider_track_id, match_method, confidence,
                is_manual, candidate_title, candidate_artist, updated_at
            ) VALUES ('spotify-1', 'soundcloud', 'legacy-id', 'manual', 1.0, 1, 'Song', 'Artist', 'now')
            """
        )
        connection.commit()

        report = run_update_workflow(
            connection,
            snapshot,
            _Search(),
            ("qobuz", "tidal", "deezer"),
            lambda _source: _Downloader(),
            tmp_path / "library",
            tmp_path / "playlists",
        )
        row = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert row["provider"] == "qobuz"
    assert row["provider_track_id"] == "qobuz-replacement"
    assert report.operational_failures == 0


def test_manual_override_rejects_retired_soundcloud_provider(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    with connect(database) as connection:
        apply_snapshot(connection, _snapshot())
        with pytest.raises(ValueError, match="unsupported resolution provider"):
            set_manual_override(connection, "spotify-1", "soundcloud", "legacy-id")


def test_save_resolution_persists_normalized_provider(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    with connect(database) as connection:
        apply_snapshot(connection, _snapshot())
        save_resolution(
            connection,
            "spotify-1",
            Resolution(
                ResolutionStatus.RESOLVED,
                Candidate(" TIDAL ", "tidal-1", "Song", "Artist"),
                MatchMethod.METADATA,
                0.95,
                "test",
            ),
        )
        row = connection.execute(
            "SELECT provider FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert row["provider"] == "tidal"


def test_legacy_cli_reports_unsupported_manual_provider_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    initialize(home / "synctify.sqlite3")
    with connect(home / "synctify.sqlite3") as connection:
        apply_snapshot(connection, _snapshot())

    result = CliRunner().invoke(
        app,
        ["resolve", "set", "spotify-1", "soundcloud", "legacy-id"],
    )

    assert result.exit_code == 2
    assert "unsupported resolution provider" in result.output
    assert "Traceback" not in result.output


class _ReconcileProvider:
    name = "fake-reconcile"
    supported_sources = frozenset({"qobuz"})

    def supports(self, source: str) -> bool:
        return source == "qobuz"

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        path = find_existing_flac(candidate, destination)
        if path is None:
            raise RuntimeError("no existing FLAC")
        return AcquiredTrack(candidate.provider, candidate.provider_track_id, path, reconciled=True)


def test_acquisition_batch_parses_existing_flac_metadata_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    first = library / "one.flac"
    second = library / "two.flac"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    calls: list[Path] = []

    def fake_read(path: Path) -> Candidate:
        calls.append(path)
        if path.name == "one.flac":
            return Candidate("local", str(path.resolve()), "One", "Artist", "Album", "ISRC1", 180_000)
        return Candidate("local", str(path.resolve()), "Two", "Artist", "Album", "ISRC2", 181_000)

    monkeypatch.setattr("synctify.providers.reconcile.read_flac_candidate", fake_read)

    database = tmp_path / "state.sqlite3"
    initialize(database)
    tasks = (
        AcquisitionTask("spotify-1", "qobuz", "q1", "One", "Artist", "Album", "ISRC1", 180_000),
        AcquisitionTask("spotify-2", "qobuz", "q2", "Two", "Artist", "Album", "ISRC2", 181_000),
    )

    with connect(database) as connection:
        # Recording updates may affect zero rows here; this test only exercises the
        # downloader/reconciliation batch boundary.
        report = acquire_tasks(connection, _ReconcileProvider(), tasks, library)

    assert report.reconciled == 2
    assert len(calls) == 2
    assert set(calls) == {first.resolve(), second.resolve()}
