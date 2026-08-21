from __future__ import annotations

from pathlib import Path
from typing import Iterable

from mutagen import MutagenError
from mutagen.flac import FLAC

from ..models import Track
from ..resolution import Candidate, ResolutionStatus, resolve_track


def _tag_values(audio: FLAC, key: str) -> tuple[str, ...]:
    if audio.tags is None:
        return ()
    values = audio.tags.get(key, [])
    return tuple(str(value).strip() for value in values if str(value).strip())


def read_flac_candidate(path: Path) -> Candidate | None:
    """Read enough FLAC metadata to reuse the deterministic track matcher."""
    try:
        audio = FLAC(path)
    except (MutagenError, OSError):
        return None

    titles = _tag_values(audio, "title")
    artists = _tag_values(audio, "artist")
    if not titles or not artists:
        return None

    albums = _tag_values(audio, "album")
    isrcs = _tag_values(audio, "isrc")
    duration_ms = None
    if audio.info is not None and audio.info.length is not None:
        duration_ms = round(float(audio.info.length) * 1000)

    return Candidate(
        provider="local",
        provider_track_id=str(path.resolve()),
        title=titles[0],
        artist=", ".join(artists),
        album=albums[0] if albums else None,
        isrc=isrcs[0] if isrcs else None,
        duration_ms=duration_ms,
    )


def _safe_flac_paths(destination: Path, paths: Iterable[Path] | None) -> tuple[Path, ...]:
    root = destination.expanduser().resolve()
    if paths is None:
        if not root.exists():
            return ()
        candidates = (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".flac"
        )
    else:
        candidates = paths

    safe: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() == ".flac":
            safe.add(resolved)
    return tuple(sorted(safe))


def find_existing_flac(
    candidate: Candidate,
    destination: Path,
    *,
    paths: Iterable[Path] | None = None,
) -> Path | None:
    """Return one safely matched existing FLAC, otherwise refuse to guess."""
    target = Track(
        spotify_id=f"reconcile:{candidate.provider}:{candidate.provider_track_id}",
        title=candidate.title,
        artist=candidate.artist,
        album=candidate.album,
        isrc=candidate.isrc,
        duration_ms=candidate.duration_ms,
    )

    local_candidates = tuple(
        metadata
        for path in _safe_flac_paths(destination, paths)
        if (metadata := read_flac_candidate(path)) is not None
    )
    resolution = resolve_track(target, local_candidates)
    if resolution.status is not ResolutionStatus.RESOLVED or resolution.candidate is None:
        return None

    matched = Path(resolution.candidate.provider_track_id).resolve()
    try:
        matched.relative_to(destination.expanduser().resolve())
    except ValueError:
        return None
    return matched if matched.is_file() else None


def output_reports_existing_file(stdout: str, stderr: str) -> bool:
    """Recognize common downloader messages that mean the requested file was skipped."""
    text = f"{stdout}\n{stderr}".casefold()
    markers = (
        "already exists",
        "already downloaded",
        "file exists",
        "exists, skipping",
        "exists; skipping",
        "skipping existing",
    )
    return any(marker in text for marker in markers)
