from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from synctify.providers.base import AcquisitionProvider
from synctify.providers.qobuz import (
    QobuzDLConfig,
    QobuzDLDownloadError,
    QobuzDLProvider,
    QobuzDLUnavailableError,
)
from synctify.resolution import Candidate


def candidate(provider: str = "qobuz") -> Candidate:
    return Candidate(
        provider=provider,
        provider_track_id="123456789",
        title="Track",
        artist="Artist",
        album="Album",
        isrc="USABC1234567",
        duration_ms=200_000,
    )


def test_qobuz_provider_conforms_to_protocol() -> None:
    provider = QobuzDLProvider()
    assert isinstance(provider, AcquisitionProvider)
    assert provider.name == "qobuz-dl"
    assert provider.supports("qobuz") is True
    assert provider.supports("tidal") is False


def test_build_download_command_uses_quality_and_destination(tmp_path: Path) -> None:
    provider = QobuzDLProvider(QobuzDLConfig(quality=27, extra_args=("--no-lrc-files",)))

    command = provider.build_download_command(
        "https://open.qobuz.com/track/123456789",
        tmp_path,
    )

    assert command == [
        "qobuz-dl",
        "dl",
        "https://open.qobuz.com/track/123456789",
        "-d",
        str(tmp_path),
        "-q",
        "27",
        "--no-lrc-files",
    ]


def test_build_download_command_rejects_non_qobuz_url(tmp_path: Path) -> None:
    provider = QobuzDLProvider()

    with pytest.raises(ValueError):
        provider.build_download_command("https://example.com/track/1", tmp_path)


def test_qobuz_dl_rejects_non_qobuz_resolution(tmp_path: Path) -> None:
    provider = QobuzDLProvider()

    with pytest.raises(ValueError, match="only supports Qobuz"):
        provider.acquire(candidate("tidal"), tmp_path)


def test_acquire_detects_new_flac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.qobuz.shutil.which", lambda _: "/usr/local/bin/qobuz-dl")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output = tmp_path / "Artist" / "Album" / "01 - Track.flac"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"flac")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    provider = QobuzDLProvider(runner=runner)
    acquired = provider.acquire(candidate(), tmp_path)

    assert acquired.provider == "qobuz"
    assert acquired.provider_track_id == "123456789"
    assert acquired.path.name == "01 - Track.flac"


def test_acquire_surfaces_qobuz_dl_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.qobuz.shutil.which", lambda _: "/usr/local/bin/qobuz-dl")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="authentication failed")

    provider = QobuzDLProvider(runner=runner)

    with pytest.raises(QobuzDLDownloadError, match="authentication failed"):
        provider.acquire(candidate(), tmp_path)


def test_missing_qobuz_dl_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.qobuz.shutil.which", lambda _: None)
    provider = QobuzDLProvider()

    with pytest.raises(QobuzDLUnavailableError):
        provider.acquire(candidate(), tmp_path)
