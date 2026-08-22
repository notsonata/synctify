from __future__ import annotations

from pathlib import Path
import tomllib

import synctify


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert version == "1.2.0"
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


def test_readme_documents_release_zip_launcher() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "synctify-1.2.0-macos.zip" in readme
    assert "./synctify.sh" in readme
    assert "./synctify.sh install" in readme
    assert "~/.local/bin/synctify" in readme
    assert "app/current" in readme
    assert "private `.venv`" in readme
    assert "synctify-1.2.0.tar.gz" in readme
    assert "matching Python wheel bundled inside the macOS ZIP" in readme
    assert "The GitHub Release also includes `SHA256SUMS`" not in readme


def test_readme_documents_upgrade_policy() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "synctify upgrade --check" in readme
    assert "synctify upgrade" in readme
    assert "Upgrade now? [Y/n]" in readme
    assert "synctify self-update" not in readme
    assert "synctify config set auto-update prompt" in readme
    assert "SYNCTIFY_AUTO_UPDATE" in readme
    assert "gh auth login" in readme


def test_readme_documents_staged_spotify_flow() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "synctify spotify fetch-playlists" in readme
    assert "synctify spotify update-tracked" in readme
    assert "Liked Songs behaves like a normal playlist" in readme
    assert "pending additions" in readme
    assert "pending removals" in readme
    assert "Track exclusions are persistent" in readme
    assert "does **not** fetch every song in every playlist" in readme
    assert "does not fetch Spotify playlists" in readme


def test_readme_documents_tui_playlist_browser_and_cancellation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Dashboard | Spotify | Unresolved | Doctor | Audit | Commands" in readme
    assert "Load / Review" in readme
    assert "Confirm Playlist" in readme
    assert "Apply Choices" in readme
    assert "Press **Esc**" in readme
    assert "loading indicator" in readme
    assert "acquire --source tidal" in readme


def test_readme_documents_safe_unimport_and_non_destructive_backups() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "only when no other imported Synctify playlist still references that track" in readme
    assert "never deletes existing rclone backup objects" in readme
    assert "copy-only semantics" in readme


def test_readme_documents_update_progress() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "report their current stage to stderr" in readme
    assert "final structured workflow report remains on stdout" in readme


def test_readme_documents_configurable_library() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "synctify config set library-dir" in readme
    assert "SYNCTIFY_LIBRARY_DIR" in readme
    assert "--library /absolute/path/to/Music" in readme


def test_readme_documents_schema_v6() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "schema v6" in readme
    assert "persistent track exclusions" in readme


def test_installed_entry_point_uses_explicit_cli_app() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["synctify"] == "synctify.app:app"
