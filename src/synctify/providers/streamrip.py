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
from .reconcile import find_existing_flac, output_reports_existing_file

STREAMRIP_REPOSITORY = "https://github.com/nathom/streamrip"
# Synctify persists only canonical FLAC audio. Streamrip also supports sources
# such as SoundCloud, but those do not provide a lossless FLAC acquisition path.
STREAMRIP_SOURCES = frozenset({"qobuz", "tidal", "deezer"})


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
    """Out-of-process Streamrip downloader for Synctify's lossless sources."""

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

    @staticmethod
    def _error_message(result: subprocess.CompletedProcess[str]) -> str:
        return result.stderr.strip() or result.stdout.strip() or "streamrip failed"

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        source = candidate.provider.strip().lower()
        if not self.supports(source):
            supported = ", ".join(sorted(self.supported_sources))
            raise ValueError(
                f"streamrip source {candidate.provider!r} is unsupported for Synctify's canonical FLAC library; choose one of: {supported}"
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

        after = {path.resolve() for path in destination.rglob("*.flac")}
        files = tuple(sorted(after - before))

        if result.returncode == 0 and len(files) == 1:
            return AcquiredTrack(
                provider=source,
                provider_track_id=candidate.provider_track_id,
                path=files[0],
            )

        may_be_existing = result.returncode == 0 or output_reports_existing_file(
            result.stdout,
            result.stderr,
        )
        if not files and may_be_existing:
            existing = find_existing_flac(candidate, destination)
            if existing is not None:
                return AcquiredTrack(
                    provider=source,
                    provider_track_id=candidate.provider_track_id,
                    path=existing,
                    reconciled=True,
                )

        if result.returncode != 0:
            raise StreamripDownloadError(self._error_message(result))
        if len(files) > 1:
            raise StreamripDownloadError(
                f"Expected one new FLAC for {source} track {candidate.provider_track_id}, found {len(files)}"
            )
        raise StreamripDownloadError(
            f"streamrip created no new FLAC for {source} track {candidate.provider_track_id}, "
            "and no unique matching existing FLAC could be reconciled"
        )
