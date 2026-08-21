from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

from ..resolution import Candidate
from .base import AcquiredTrack

STREAMRIP_REPOSITORY = "https://github.com/nathom/streamrip"
STREAMRIP_SOURCES = frozenset({"qobuz", "tidal", "deezer", "soundcloud"})


class StreamripUnavailableError(RuntimeError):
    pass


class StreamripDownloadError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class StreamripConfig:
    executable: str = "rip"
    quality: int = 4
    extra_args: tuple[str, ...] = ()


class StreamripProvider:
    """Out-of-process Streamrip downloader for supported source services."""

    name = "streamrip"
    supported_sources = STREAMRIP_SOURCES

    def __init__(self, config: StreamripConfig | None = None, *, runner: Runner = subprocess.run) -> None:
        self.config = config or StreamripConfig()
        self._runner = runner

    def supports(self, source: str) -> bool:
        return source.strip().lower() in self.supported_sources

    def is_available(self) -> bool:
        return shutil.which(self.config.executable) is not None

    def require_available(self) -> None:
        if not self.is_available():
            raise StreamripUnavailableError(
                f"{self.config.executable!r} was not found on PATH. Install streamrip first: {STREAMRIP_REPOSITORY}"
            )

    def build_download_command(self, source_file: Path, destination: Path) -> list[str]:
        if self.config.quality not in {0, 1, 2, 3, 4}:
            raise ValueError("Streamrip quality must be between 0 and 4")
        return [
            self.config.executable,
            "--folder",
            str(destination),
            "--no-db",
            "--quality",
            str(self.config.quality),
            "--no-progress",
            *self.config.extra_args,
            "file",
            str(source_file),
        ]

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        source = candidate.provider.strip().lower()
        if not self.supports(source):
            supported = ", ".join(sorted(self.supported_sources))
            raise ValueError(
                f"streamrip source {candidate.provider!r} is unsupported; choose one of: {supported}"
            )

        self.require_available()
        destination.mkdir(parents=True, exist_ok=True)
        before = {path.resolve() for path in destination.rglob("*.flac")}

        source_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="synctify-streamrip-",
                delete=False,
            ) as handle:
                json.dump(
                    [
                        {
                            "source": source,
                            "media_type": "track",
                            "id": candidate.provider_track_id,
                        }
                    ],
                    handle,
                )
                source_file = Path(handle.name)

            result = self._runner(
                self.build_download_command(source_file, destination),
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            if source_file is not None:
                source_file.unlink(missing_ok=True)

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "streamrip failed"
            raise StreamripDownloadError(message)

        after = {path.resolve() for path in destination.rglob("*.flac")}
        files = tuple(sorted(after - before))
        if len(files) != 1:
            raise StreamripDownloadError(
                f"Expected one new FLAC for {source} track {candidate.provider_track_id}, found {len(files)}"
            )
        return AcquiredTrack(
            provider=source,
            provider_track_id=candidate.provider_track_id,
            path=files[0],
        )
