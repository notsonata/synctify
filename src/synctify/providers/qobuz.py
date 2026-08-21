from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from ..models import Track
from ..resolution import Candidate
from .base import AcquiredTrack


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
    """Out-of-process adapter for Sei969/qobuz-dl."""

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

    def search(self, track: Track) -> Sequence[Candidate]:
        """Search remains disabled until qobuz-dl exposes stable machine-readable output."""
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
        result = self._runner(
            self.build_download_command(qobuz_url, destination),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "qobuz-dl failed"
            raise QobuzDLDownloadError(message)
        after = {path.resolve() for path in destination.rglob("*.flac")}
        return tuple(sorted(after - before))

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        qobuz_url = f"https://open.qobuz.com/track/{candidate.provider_track_id}"
        files = self.acquire_url(qobuz_url, destination)
        if len(files) != 1:
            raise QobuzDLDownloadError(
                f"Expected one new FLAC for track {candidate.provider_track_id}, found {len(files)}"
            )
        return AcquiredTrack(
            provider=self.name,
            provider_track_id=candidate.provider_track_id,
            path=files[0],
        )
