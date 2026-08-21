from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from synctify.cli_entry import app
from synctify.db import connect, initialize
from synctify.providers.base import AcquiredTrack
from synctify.resolution import Candidate, set_manual_override
from synctify.spotify.ingest import (
    PlaylistEntry,
    SpotifyPlaylist,
    SpotifySnapshot,
    SpotifyTrack,
)
from synctify.workflow import preview_update_workflow, run_update_workflow


class FakeSearch:
    name = "fake-search"
    supported_sources = frozenset({"qobuz", "tidal", "deezer", "soundcloud"})

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def search(self, track, source: str, *, limit: int | None = None):
        self.calls.append((track.spotify_id, source))
        return [
            Candidate(
                provider=source,
                provider_track_id=f"{source}-123",
                title=track.title,
                artist=track.artist,
            )
        ]


class FakeDownloader:
    supported_sources = frozenset({"qobuz", "tidal", "deezer", "soundcloud"})

    def __init__(self, source: str, calls: list[str], *, fail: bool = False) -> None:
        self.source = source
        self.calls = calls
        self.fail = fail
        self.name = "qobuz-dl" if source == "qobuz" else "streamrip"

    def supports(self, source: str) -> bool:
        return source in self.supported_sources

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        self.calls.append(candidate.provider_track_id)
        if self.fail:
            raise RuntimeError("simulated downloader failure")
        path = destination / "Artist" / "Album" / f"{candidate.provider_track_id}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(candidate.provider_track_id.encode())
        return AcquiredTrack(candidate.provider, candidate.provider_track_id, path)


def _snapshot(*, spotify_id: str = "spotify-1") -> SpotifySnapshot:
    track = SpotifyTrack(
        spotify_id=spotify_id,
        title="Paranoid Android",
        artist="Radiohead",
        album="OK Computer",
        duration_ms=386_000,
        isrc="GBAYE9701376",
    )
    playlist = SpotifyPlaylist(
        spotify_id="playlist-1",
        name="Driving",
        tracks=(PlaylistEntry(track),),
        snapshot_id="snapshot-1",
        source_kind="playlist",
        owner_id="user-1",
    )
    return SpotifySnapshot("user-1", (playlist,))


def test_full_update_resolves_acquires_and_builds_playlist(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    initialize(database)
    download_calls: list[str] = []

    def factory(source: str):
        return FakeDownloader(source, download_calls)

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            FakeSearch(),
            "qobuz",
            factory,
            library,
            playlists,
        )
        resolution = connection.execute(
            "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = 'spotify-1'"
        ).fetchone()
        track = connection.execute(
            "SELECT local_path, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert resolution["provider"] == "qobuz"
    assert resolution["provider_track_id"] == "qobuz-123"
    assert track["status"] == "local"
    assert Path(track["local_path"]).is_file()
    assert download_calls == ["qobuz-123"]
    assert report.acquisitions[0].succeeded == 1
    assert report.playlists is not None
    assert report.playlists.written == 1
    output = report.playlists.results[0].output
    assert output is not None and output.is_file()


def test_preview_simulates_new_resolutions_then_rolls_back_everything(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)
    download_calls: list[str] = []

    def factory(source: str):
        return FakeDownloader(source, download_calls)

    with connect(database) as connection:
        report = preview_update_workflow(
            connection,
            _snapshot(),
            FakeSearch(),
            "qobuz",
            factory,
        )
        tracks = connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        playlists = connection.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
        resolutions = connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0]

    assert report.dry_run is True
    assert report.resolution.resolved == 1
    assert len(report.acquisitions) == 1
    assert report.acquisitions[0].planned == 1
    assert report.playlist_readiness is not None
    assert report.playlist_readiness.incomplete == 1
    assert download_calls == []
    assert tracks == 0
    assert playlists == 0
    assert resolutions == 0


def test_update_acquires_existing_non_qobuz_resolution_with_streamrip_group(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    playlists = tmp_path / "playlists"
    initialize(database)
    download_calls: list[str] = []

    with connect(database) as connection:
        connection.execute(
            """
            INSERT INTO tracks(spotify_id, title, artist, album, isrc, duration_ms)
            VALUES ('spotify-1', 'Paranoid Android', 'Radiohead', 'OK Computer', 'GBAYE9701376', 386000)
            """
        )
        set_manual_override(connection, "spotify-1", "tidal", "tidal-manual")
        connection.commit()

        report = run_update_workflow(
            connection,
            _snapshot(),
            FakeSearch(),
            "qobuz",
            lambda source: FakeDownloader(source, download_calls),
            library,
            playlists,
        )

    assert report.resolution.attempts == ()
    assert len(report.acquisitions) == 1
    assert report.acquisitions[0].source == "tidal"
    assert report.acquisitions[0].downloader == "streamrip"
    assert report.acquisitions[0].succeeded == 1
    assert download_calls == ["tidal-manual"]


def test_download_failure_is_reported_and_playlist_build_still_runs(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    initialize(database)

    with connect(database) as connection:
        report = run_update_workflow(
            connection,
            _snapshot(),
            FakeSearch(),
            "qobuz",
            lambda source: FakeDownloader(source, [], fail=True),
            tmp_path / "library",
            tmp_path / "playlists",
        )

    assert report.operational_failures == 1
    assert report.acquisitions[0].failed == 1
    assert report.playlists is not None
    assert report.playlists.incomplete == 1
    assert report.playlists.written == 0


def test_cli_update_dry_run_uses_coordinated_command_and_leaves_db_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setattr("synctify.cli_entry._fetch_update_snapshot", lambda settings: _snapshot())
    monkeypatch.setattr(
        "synctify.cli_entry.StreamripCatalogSearch.search",
        lambda self, track, source, limit=None: [
            Candidate(source, f"{source}-123", track.title, track.artist)
        ],
    )

    result = CliRunner().invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Acquisition" in result.output
    assert "Preview only" in result.output
    with connect(home / "synctify.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM track_resolutions").fetchone()[0] == 0
