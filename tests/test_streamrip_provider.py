from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from synctify.providers.base import AcquisitionProvider
from synctify.providers.streamrip import (
    StreamripConfig,
    StreamripDownloadError,
    StreamripProvider,
    StreamripUnavailableError,
)
from synctify.resolution import Candidate


def candidate() -> Candidate:
    return Candidate(
        provider="qobuz",
        provider_track_id="123456789",
        title="Track",
        artist="Artist",
        album="Album",
        isrc="USABC1234567",
        duration_ms=200_000,
    )


def test_streamrip_provider_conforms_to_protocol() -> None:
    assert isinstance(StreamripProvider(), AcquisitionProvider)


def test_streamrip_command_uses_managed_download_flags(tmp_path: Path) -> None:
    provider = StreamripProvider(StreamripConfig(quality=4))

    command = provider.build_download_command(
        "https://open.qobuz.com/track/123456789",
        tmp_path,
    )

    assert command == [
        "rip",
        "--folder",
        str(tmp_path),
        "--no-db",
        "--quality",
        "4",
        "--no-progress",
        "url",
        "https://open.qobuz.com/track/123456789",
    ]


def test_streamrip_acquire_detects_new_flac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.streamrip.shutil.which", lambda _: "/opt/homebrew/bin/rip")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = tmp_path / "Artist" / "Album" / "01 - Track.flac"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"flac")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    acquired = StreamripProvider(runner=runner).acquire(candidate(), tmp_path)

    assert acquired.provider == "qobuz"
    assert acquired.provider_track_id == "123456789"
    assert acquired.path.name == "01 - Track.flac"


def test_streamrip_surfaces_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.streamrip.shutil.which", lambda _: "/opt/homebrew/bin/rip")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="qobuz login failed")

    with pytest.raises(StreamripDownloadError, match="qobuz login failed"):
        StreamripProvider(runner=runner).acquire(candidate(), tmp_path)


def test_missing_streamrip_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.streamrip.shutil.which", lambda _: None)

    with pytest.raises(StreamripUnavailableError):
        StreamripProvider().acquire(candidate(), tmp_path)
