from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from synctify.providers.base import AcquisitionProvider
from synctify.providers.streamrip import (
    STREAMRIP_SOURCES,
    StreamripConfig,
    StreamripDownloadError,
    StreamripProvider,
    StreamripUnavailableError,
)
from synctify.resolution import Candidate


def candidate(provider: str = "qobuz", provider_track_id: str = "123456789") -> Candidate:
    return Candidate(
        provider=provider,
        provider_track_id=provider_track_id,
        title="Track",
        artist="Artist",
        album="Album",
        isrc="USABC1234567",
        duration_ms=200_000,
    )


def test_streamrip_provider_conforms_to_protocol_and_lists_sources() -> None:
    provider = StreamripProvider()
    assert isinstance(provider, AcquisitionProvider)
    assert provider.name == "streamrip"
    assert provider.supported_sources == frozenset(
        {"qobuz", "tidal", "deezer", "soundcloud"}
    )
    assert provider.supported_sources == STREAMRIP_SOURCES


def test_streamrip_command_uses_exact_id_file(tmp_path: Path) -> None:
    provider = StreamripProvider(StreamripConfig(quality=3))
    source_file = tmp_path / "source.json"
    source_file.write_text("[]", encoding="utf-8")

    command = provider.build_download_command(source_file, tmp_path)

    assert command == [
        "rip",
        "--folder",
        str(tmp_path),
        "--no-db",
        "--quality",
        "3",
        "--no-progress",
        "file",
        str(source_file),
    ]


@pytest.mark.parametrize("source", ["qobuz", "tidal", "deezer", "soundcloud"])
def test_streamrip_acquire_passes_source_and_exact_track_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
) -> None:
    monkeypatch.setattr(
        "synctify.providers.streamrip.shutil.which",
        lambda _: "/opt/homebrew/bin/rip",
    )
    captured: dict[str, object] = {}

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        source_file = Path(command[-1])
        captured["payload"] = json.loads(source_file.read_text(encoding="utf-8"))
        output = tmp_path / "Artist" / "Album" / f"01 - {source}.flac"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"flac")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    acquired = StreamripProvider(runner=runner).acquire(
        candidate(source, f"{source}-id"),
        tmp_path,
    )

    assert captured["payload"] == [
        {
            "source": source,
            "media_type": "track",
            "id": f"{source}-id",
        }
    ]
    assert acquired.provider == source
    assert acquired.provider_track_id == f"{source}-id"


def test_streamrip_rejects_unsupported_source(tmp_path: Path) -> None:
    provider = StreamripProvider()

    with pytest.raises(ValueError, match="unsupported"):
        provider.acquire(candidate("spotify"), tmp_path)


def test_streamrip_surfaces_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "synctify.providers.streamrip.shutil.which",
        lambda _: "/opt/homebrew/bin/rip",
    )

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="tidal login failed")

    with pytest.raises(StreamripDownloadError, match="tidal login failed"):
        StreamripProvider(runner=runner).acquire(candidate("tidal"), tmp_path)


def test_missing_streamrip_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("synctify.providers.streamrip.shutil.which", lambda _: None)

    with pytest.raises(StreamripUnavailableError):
        StreamripProvider().acquire(candidate("deezer"), tmp_path)
