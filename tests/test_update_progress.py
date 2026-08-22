from __future__ import annotations

from pathlib import Path

import pytest

import synctify.cli_entry as cli_entry
from synctify.workflow import _stderr_progress


ROOT = Path(__file__).resolve().parents[1]


def test_update_snapshot_reports_progress_before_loading_spotify_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class SettingsStub:
        spotify_config_path = Path("spotify.json")

    def fail_load(_path: Path):
        raise RuntimeError("stop after progress message")

    monkeypatch.setattr(cli_entry.SpotifyOAuthConfig, "load", fail_load)

    with pytest.raises(RuntimeError, match="stop after progress message"):
        cli_entry._fetch_update_snapshot(SettingsStub())  # type: ignore[arg-type]

    assert "[update] Fetching Spotify desired state..." in capsys.readouterr().err


def test_workflow_progress_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    _stderr_progress("Resolving 12 track(s) via qobuz...")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[update] Resolving 12 track(s) via qobuz...\n"


def test_update_workflow_exposes_real_stage_messages() -> None:
    workflow = (ROOT / "src" / "synctify" / "workflow.py").read_text(encoding="utf-8")

    assert "Resolving {len(pending)} track(s) via {source}..." in workflow
    assert "Planning downloads..." in workflow
    assert "Checking playlist readiness..." in workflow
    assert "Building playlists..." in workflow
