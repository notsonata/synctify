from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from ..resolution import Candidate
from .base import AcquiredTrack
from .reconcile import find_existing_flac, output_reports_existing_file

QOBUZ_DL_REPOSITORY = "https://github.com/Sei969/qobuz-dl"


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
    """Invoke a separately cloned/installed Sei969/qobuz-dl executable."""

    name = "qobuz-dl"
    supported_sources = frozenset({"qobuz"})

    def __init__(self, config: QobuzDLConfig | None = None, *, runner: Runner = subprocess.run) -> None:
        self.config = config or QobuzDLConfig()
        self._runner = runner

    def supports(self, source: str) -> bool:
        return source.strip().lower() in self.supported_sources

    def is_available(self) -> bool:
        return shutil.which(self.config.executable) is not None

    def require_available(self) -> None:
        if not self.is_available():
            raise QobuzDLUnavailableError(
                f"{self.config.executable!r} was not found. Clone {QOBUZ_DL_REPOSITORY}, "
                "complete its upstream setup, then put qobuz-dl on PATH or pass its executable path."
            )

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

    def _run_download(
        self,
        qobuz_url: str,
        destination: Path,
    ) -> tuple[subprocess.CompletedProcess[str], tuple[Path, ...]]:
        self.require_available()
        destination.mkdir(parents=True, exist_ok=True)
        before = {path.resolve() for path in destination.rglob("*.flac")}
        result = self._runner(
            self.build_download_command(qobuz_url, destination),
            text=True,
            capture_output=True,
            check=False,
        )
        after = {path.resolve() for path in destination.rglob("*.flac")}
        return result, tuple(sorted(after - before))

    @staticmethod
    def _error_message(result: subprocess.CompletedProcess[str]) -> str:
        return result.stderr.strip() or result.stdout.strip() or "qobuz-dl failed"

    def acquire_url(self, qobuz_url: str, destination: Path) -> tuple[Path, ...]:
        result, files = self._run_download(qobuz_url, destination)
        if result.returncode != 0:
            raise QobuzDLDownloadError(self._error_message(result))
        return files

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        if not self.supports(candidate.provider):
            raise ValueError(
                f"qobuz-dl only supports Qobuz resolutions, not {candidate.provider!r}"
            )

        qobuz_url = f"https://open.qobuz.com/track/{candidate.provider_track_id}"
        result, files = self._run_download(qobuz_url, destination)

        if result.returncode == 0 and len(files) == 1:
            return AcquiredTrack(
                provider=candidate.provider,
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
                    provider=candidate.provider,
                    provider_track_id=candidate.provider_track_id,
                    path=existing,
                    reconciled=True,
                )

        if result.returncode != 0:
            raise QobuzDLDownloadError(self._error_message(result))
        if len(files) > 1:
            raise QobuzDLDownloadError(
                f"Expected one new FLAC for track {candidate.provider_track_id}, found {len(files)}"
            )
        raise QobuzDLDownloadError(
            f"qobuz-dl created no new FLAC for track {candidate.provider_track_id}, "
            "and no unique matching existing FLAC could be reconciled"
        )
