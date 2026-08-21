from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from .base import AcquiredTrack, Candidate, SourceTrack


class QobuzDLUnavailableError(RuntimeError):
    pass


class QobuzDLDownloadError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class QobuzDLConfig:
    executable: str = "qobuz-dl"
    quality: int = 27
    extra_args: tuple[str, ...] = ()


class QobuzDLProvider:
    """Subprocess adapter for Sei969/qobuz-dl.

    Search integration will be added separately. This adapter acquires a
    previously resolved Qobuz URL and keeps the GPL application out-of-process.
    """

    name = "qobuz"

    def __init__(self, config: QobuzDLConfig | None = None, *, runner: Runner = subprocess.run) -> None:
        self.config = config or QobuzDLConfig()
        self._runner = runner

    def is_available(self) -> bool:
        return shutil.which(self.config.executable) is not None

    def require_available(self) -> None:
        if not self.is_available():
            raise QobuzDLUnavailableError(
                f"{self.config.executable!r} was not found on PATH. Install qobuz-dl-ultimate first."
            )

    def search(self, track: SourceTrack) -> Sequence[Candidate]:
        """Search is intentionally unavailable until a stable machine-readable API is wired."""
        return ()

    def build_download_command(self, qobuz_url: str, destination: Path) -> list[str]:
        if not qobuz_url.startswith(("https://www.qobuz.com/", "https://open.qobuz.com/")):
            raise ValueError("Expected a Qobuz track or album URL")
        return [
            self.config.executable,
            "dl",
            qobuz_url,
            "-d",
            str(destination),
            "-q",
            str(self.config.quality),
            *self.config.extra_args,
        ]

    def acquire_url(self, qobuz_url: str, destination: Path) -> tuple[Path, ...]:
        self.require_available()
        destination.mkdir(parents=True, exist_ok=True)
        before = {path.resolve() for path in destination.rglob("*.flac")}
        command = self.build_download_command(qobuz_url, destination)
        result = self._runner(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "qobuz-dl failed"
            raise QobuzDLDownloadError(message)
        after = {path.resolve() for path in destination.rglob("*.flac")}
        return tuple(sorted(after - before))

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        url = candidate.extra.get("url") if candidate.extra else None
        if not isinstance(url, str):
            raise ValueError("Qobuz candidate requires an extra['url'] value")
        files = self.acquire_url(url, destination)
        if len(files) != 1:
            raise QobuzDLDownloadError(
                f"Expected one new FLAC for track {candidate.provider_track_id}, found {len(files)}"
            )
        return AcquiredTrack(candidate=candidate, path=files[0])
