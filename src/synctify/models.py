from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class Track:
    spotify_id: str
    title: str
    artist: str
    album: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None
    local_path: Path | None = None


@dataclass(slots=True, frozen=True)
class Playlist:
    spotify_id: str
    name: str
    tracks: tuple[Track, ...]
    snapshot_id: str | None = None
