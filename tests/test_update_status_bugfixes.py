from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import synctify.entrypoint as entrypoint
from synctify.db import connect, initialize
from synctify.entrypoint import app
from synctify.library_update_cli import _primary_failures
from synctify.playlists import PlaylistBuildReport, PlaylistBuildResult
from synctify.spotify.ingest import SpotifySnapshot
from synctify.spotify.state import ChangePlan
from synctify.workflow import UpdateWorkflowReport


runner = CliRunner()


def _empty_change_plan() -> ChangePlan:
    return ChangePlan((), (), (), (), 0, 0, 0, ())


def _make_legacy_database(path: Path) -> None:
    initialize(path)
    with connect(path) as connection:
        connection.execute(
            "UPDATE metadata SET value = '2' WHERE key = 'schema_version'"
        )
        connection.execute("DROP TABLE track_resolutions")
        connection.execute("DROP TABLE generated_playlists")
        connection.commit()


def test_update_dry_run_does_not_create_synctify_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "missing-home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setattr(
        entrypoint.cli_entry_module,
        "_fetch_update_snapshot",
        lambda _settings: SpotifySnapshot("me", ()),
    )

    result = runner.invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Preview only" in result.output
    assert not home.exists()


def test_update_dry_run_clones_and_migrates_only_temporary_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "synctify.sqlite3"
    _make_legacy_database(database)
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    monkeypatch.setattr(
        entrypoint.cli_entry_module,
        "_fetch_update_snapshot",
        lambda _settings: SpotifySnapshot("me", ()),
    )

    result = runner.invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 0, result.output
    with connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert version == "2"
    assert "track_resolutions" not in tables
    assert "generated_playlists" not in tables
    assert not (home / "library").exists()
    assert not (home / "playlists").exists()


def test_status_tolerates_legacy_database_without_resolution_table(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    database = home / "synctify.sqlite3"
    _make_legacy_database(database)
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert "Resolutions: 0" in result.output
    assert "Pending DL:  0" in result.output
    with connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert "track_resolutions" not in tables
    assert version == "2"


def test_incomplete_playlist_is_warning_not_primary_operational_failure() -> None:
    incomplete = PlaylistBuildReport(
        (
            PlaylistBuildResult(
                spotify_id="playlist-1",
                name="Playlist",
                output=None,
                total_tracks=1,
                written_tracks=0,
                missing_tracks=("spotify-track",),
            ),
        )
    )
    strict = UpdateWorkflowReport(
        spotify=_empty_change_plan(),
        resolution_sources=("qobuz",),
        resolutions=(),
        acquisitions=(),
        playlist_readiness=None,
        playlists=incomplete,
        dry_run=False,
        allow_partial=False,
    )
    partial = UpdateWorkflowReport(
        spotify=_empty_change_plan(),
        resolution_sources=("qobuz",),
        resolutions=(),
        acquisitions=(),
        playlist_readiness=None,
        playlists=incomplete,
        dry_run=False,
        allow_partial=True,
    )

    # The workflow still records strict incompleteness for callers that care,
    # but the installed update command no longer turns that review state into
    # status 2 unless an actual provider/search/download error occurred.
    assert strict.strict_playlist_failures == 1
    assert strict.operational_failures == 1
    assert _primary_failures(strict) == 0
    assert partial.strict_playlist_failures == 0
    assert partial.operational_failures == 0
    assert _primary_failures(partial) == 0
