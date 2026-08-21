from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from ..models import Track
from ..resolution import Candidate


@dataclass(slots=True, frozen=True)
class AcquiredTrack:
    provider: str
    provider_track_id: str
    path: Path


@runtime_checkable
class AcquisitionProvider(Protocol):
    """Provider boundary used by matching now and downloading later."""

    name: str

    def search(self, track: Track) -> Sequence[Candidate]:
        """Return provider candidates for a Spotify-derived track."""
        ...

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        """Acquire one resolved candidate into the canonical library."""
        ...
