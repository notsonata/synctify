from __future__ import annotations

from pathlib import Path
import tomllib

import synctify


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert version == "1.0.0"
    assert synctify.__version__ == version
    assert f"**Current version: {version}**" in readme


def test_readme_does_not_advertise_retired_soundcloud_workflows() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Qobuz → Tidal → Deezer → SoundCloud" not in readme
    assert "--source soundcloud" not in readme
    assert "--sources qobuz,tidal,deezer,soundcloud" not in readme
    assert "Streamrip → Qobuz / Tidal / Deezer / SoundCloud" not in readme


def test_readme_describes_current_automatic_resolution_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Streamrip is required for automatic catalog resolution" in readme
    assert "Automatic Streamrip resolution currently requires a usable Spotify ISRC" in readme
    assert "same provider track ID appears in both query result sets" in readme
    assert "Tracks without a Spotify ISRC" in readme


def test_installed_entry_point_uses_explicit_cli_app() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["synctify"] == "synctify.app:app"
