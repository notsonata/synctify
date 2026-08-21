from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..resolution import Candidate


@dataclass(slots=True, frozen=True)
class AcquiredTrack:
    """One local file acquired from a resolved source-service recording."""

    provider: str
    provider_track_id: str
    path: Path


@runtime_checkable
class AcquisitionProvider(Protocol):
    """External downloader boundary.

    ``name`` identifies the downloader application (for example ``qobuz-dl``
    or ``streamrip``). Candidate ``provider`` values identify source services
    such as Qobuz, Tidal, Deezer, or SoundCloud. Keeping these concepts
    separate lets one downloader support multiple source services without
    leaking downloader choice into Synctify's resolution state.
    """

    name: str
    supported_sources: frozenset[str]

    def supports(self, source: str) -> bool:
        """Return whether this downloader can acquire from ``source``."""
        ...

    def acquire(self, candidate: Candidate, destination: Path) -> AcquiredTrack:
        """Acquire one resolved candidate into the canonical library."""
        ...
