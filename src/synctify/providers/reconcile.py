from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterable, Iterator

from ..models import Track
from ..resolution import Candidate, ResolutionStatus, resolve_track


class FLACMetadataError(ValueError):
    pass


def _parse_streaminfo(payload: bytes) -> int | None:
    if len(payload) != 34:
        raise FLACMetadataError("invalid STREAMINFO block")
    packed = int.from_bytes(payload[10:18], "big")
    sample_rate = (packed >> 44) & 0xFFFFF
    total_samples = packed & ((1 << 36) - 1)
    if not sample_rate or not total_samples:
        return None
    return round(total_samples * 1000 / sample_rate)


def _parse_vorbis_comments(payload: bytes) -> dict[str, list[str]]:
    offset = 0

    def take_u32() -> int:
        nonlocal offset
        if offset + 4 > len(payload):
            raise FLACMetadataError("truncated Vorbis comment block")
        value = int.from_bytes(payload[offset : offset + 4], "little")
        offset += 4
        return value

    vendor_length = take_u32()
    if offset + vendor_length > len(payload):
        raise FLACMetadataError("truncated Vorbis vendor string")
    offset += vendor_length

    comments: dict[str, list[str]] = {}
    count = take_u32()
    for _ in range(count):
        length = take_u32()
        if offset + length > len(payload):
            raise FLACMetadataError("truncated Vorbis comment")
        raw = payload[offset : offset + length]
        offset += length
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip().casefold()
        value = value.strip()
        if key and value:
            comments.setdefault(key, []).append(value)
    return comments


def read_flac_candidate(path: Path) -> Candidate | None:
    """Read FLAC STREAMINFO and Vorbis comments without external libraries."""
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"fLaC":
                return None

            duration_ms: int | None = None
            tags: dict[str, list[str]] = {}
            while True:
                header = handle.read(4)
                if len(header) != 4:
                    raise FLACMetadataError("truncated FLAC metadata header")
                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                length = int.from_bytes(header[1:4], "big")
                payload = handle.read(length)
                if len(payload) != length:
                    raise FLACMetadataError("truncated FLAC metadata block")

                if block_type == 0:
                    duration_ms = _parse_streaminfo(payload)
                elif block_type == 4:
                    for key, values in _parse_vorbis_comments(payload).items():
                        tags.setdefault(key, []).extend(values)

                if is_last:
                    break
    except (OSError, FLACMetadataError):
        return None

    titles = tags.get("title", [])
    artists = tags.get("artist", [])
    if not titles or not artists:
        return None

    albums = tags.get("album", [])
    isrcs = tags.get("isrc", [])
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


class FLACReconciliationIndex:
    """One-batch cache of parsed canonical FLAC metadata."""

    def __init__(self, destination: Path) -> None:
        self.root = destination.expanduser().resolve()
        self._candidates: dict[Path, Candidate] = {}
        for path in _safe_flac_paths(self.root, None):
            self.add_path(path)

    def add_path(self, path: Path) -> None:
        try:
            resolved = path.expanduser().resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return
        if resolved in self._candidates or not resolved.is_file() or resolved.suffix.lower() != ".flac":
            return
        metadata = read_flac_candidate(resolved)
        if metadata is not None:
            self._candidates[resolved] = metadata

    def find(self, candidate: Candidate) -> Path | None:
        target = Track(
            spotify_id=f"reconcile:{candidate.provider}:{candidate.provider_track_id}",
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
            isrc=candidate.isrc,
            duration_ms=candidate.duration_ms,
        )
        resolution = resolve_track(target, tuple(self._candidates.values()))
        if resolution.status is not ResolutionStatus.RESOLVED or resolution.candidate is None:
            return None
        matched = Path(resolution.candidate.provider_track_id).resolve()
        try:
            matched.relative_to(self.root)
        except ValueError:
            return None
        return matched if matched.is_file() else None


_ACTIVE_INDEX: ContextVar[FLACReconciliationIndex | None] = ContextVar(
    "synctify_flac_reconciliation_index",
    default=None,
)


@contextmanager
def reconciliation_batch(destination: Path) -> Iterator[FLACReconciliationIndex]:
    """Parse the canonical library once and reuse metadata for one acquisition batch."""
    index = FLACReconciliationIndex(destination)
    token = _ACTIVE_INDEX.set(index)
    try:
        yield index
    finally:
        _ACTIVE_INDEX.reset(token)


def find_existing_flac(
    candidate: Candidate,
    destination: Path,
    *,
    paths: Iterable[Path] | None = None,
) -> Path | None:
    """Return one safely matched existing FLAC, otherwise refuse to guess."""
    root = destination.expanduser().resolve()
    active = _ACTIVE_INDEX.get()
    if paths is None and active is not None and active.root == root:
        return active.find(candidate)

    index = FLACReconciliationIndex.__new__(FLACReconciliationIndex)
    index.root = root
    index._candidates = {}
    for path in _safe_flac_paths(root, paths):
        index.add_path(path)
    return index.find(candidate)


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
