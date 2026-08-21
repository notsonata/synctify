from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from ..models import Track
from ..resolution import Candidate
from .base import AcquiredTrack

STREAMRIP_REPOSITORY = "https://github.com/nathom/streamrip"


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
    """Out-of-process Qobuz acquisition adapter for streamrip."""

    name = "qobuz"

    def __init__(self, config: StreamripConfig | None = None, *, runner: Runner = subprocess.run) -> None:
        self.config = config or StreamripConfig()
        self._runner = runner

    def is_available(self) -> bool:
        return shutil.which(self.config.executable) is not None

    def require_available(self) -> None:
        if not self.is_available():
            raise StreamripUnavailableError(
                f"{self.config.executable!r} was not found on PATH. Install streamrip first: {STREAMRIP_REPOSITORY}"
            )

    def search(self, track: Track) -> Sequence[Candidate]:
        """Search remains outside Synctify until a stable machine-readable contract is required."""
        return ()

    def build_download_command(self, qobuz_url: str, destination: Path) -> list[str]:
        if not qobuz_url.startswith(("https://www.qobuz.com/", "https://open.qobuz.com/")):
            raise ValueError("Expected a Qobuz track or album URL")
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
            "url",
            qobuz_url,
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
            message = result.stderr.strip() or result.stdout.strip() or "streamrip failed"
            raise StreamripDownloadError(message)
        after = {path.resolve() for path in destination.rglob("*.flac")}
        return tuple(sorted(after - before))

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        qobuz_url = f"https://open.qobuz.com/track/{candidate.provider_track_id}"
        files = self.acquire_url(qobuz_url, destination)
        if len(files) != 1:
            raise StreamripDownloadError(
                f"Expected one new FLAC for track {candidate.provider_track_id}, found {len(files)}"
            )
        return AcquiredTrack(
            provider=self.name,
            provider_track_id=candidate.provider_track_id,
            path=files[0],
        )
