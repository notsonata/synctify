from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Sequence

from .models import Track


class MatchMethod(StrEnum):
    MANUAL = "manual"
    ISRC = "isrc"
    METADATA = "metadata"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(slots=True, frozen=True)
class Candidate:
    provider: str
    provider_track_id: str
    title: str
    artist: str
    album: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None


@dataclass(slots=True, frozen=True)
class ManualOverride:
    provider: str
    provider_track_id: str


@dataclass(slots=True, frozen=True)
class Resolution:
    status: ResolutionStatus
    candidate: Candidate | None
    method: MatchMethod | None
    confidence: float
    reason: str


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def normalize_isrc(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _similarity(left: str | None, right: str | None) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def _duration_score(left_ms: int | None, right_ms: int | None) -> float | None:
    if left_ms is None or right_ms is None:
        return None
    diff = abs(left_ms - right_ms)
    if diff <= 2_000:
        return 1.0
    if diff <= 5_000:
        return 0.85
    if diff <= 10_000:
        return 0.5
    if diff <= 20_000:
        return 0.2
    return 0.0


def metadata_score(track: Track, candidate: Candidate) -> float:
    weighted: list[tuple[float, float]] = [
        (0.45, _similarity(track.title, candidate.title)),
        (0.40, _similarity(track.artist, candidate.artist)),
    ]
    if track.album and candidate.album:
        weighted.append((0.05, _similarity(track.album, candidate.album)))
    duration = _duration_score(track.duration_ms, candidate.duration_ms)
    if duration is not None:
        weighted.append((0.10, duration))
    total_weight = sum(weight for weight, _ in weighted)
    return sum(weight * score for weight, score in weighted) / total_weight


def resolve_track(
    track: Track,
    candidates: Sequence[Candidate],
    *,
    override: ManualOverride | None = None,
    auto_threshold: float = 0.88,
    ambiguity_margin: float = 0.04,
) -> Resolution:
    if override is not None:
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.provider == override.provider
                and candidate.provider_track_id == override.provider_track_id
            ),
            Candidate(
                provider=override.provider,
                provider_track_id=override.provider_track_id,
                title=track.title,
                artist=track.artist,
                album=track.album,
                isrc=track.isrc,
                duration_ms=track.duration_ms,
            ),
        )
        return Resolution(
            ResolutionStatus.RESOLVED,
            selected,
            MatchMethod.MANUAL,
            1.0,
            "manual override",
        )

    if not candidates:
        return Resolution(ResolutionStatus.UNRESOLVED, None, None, 0.0, "no candidates")

    source_isrc = normalize_isrc(track.isrc)
    exact = [
        candidate
        for candidate in candidates
        if source_isrc and normalize_isrc(candidate.isrc) == source_isrc
    ]
    if exact:
        ranked = sorted(exact, key=lambda item: metadata_score(track, item), reverse=True)
        if len(ranked) > 1:
            top = metadata_score(track, ranked[0])
            second = metadata_score(track, ranked[1])
            if top - second < ambiguity_margin:
                return Resolution(
                    ResolutionStatus.AMBIGUOUS,
                    None,
                    MatchMethod.ISRC,
                    1.0,
                    "multiple exact-ISRC candidates are too similar",
                )
        return Resolution(
            ResolutionStatus.RESOLVED,
            ranked[0],
            MatchMethod.ISRC,
            1.0,
            "exact ISRC match",
        )

    ranked_scores = sorted(
        ((metadata_score(track, candidate), candidate) for candidate in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    top_score, top_candidate = ranked_scores[0]
    if top_score < auto_threshold:
        return Resolution(
            ResolutionStatus.UNRESOLVED,
            None,
            MatchMethod.METADATA,
            top_score,
            "best metadata candidate is below auto-match threshold",
        )
    if len(ranked_scores) > 1 and top_score - ranked_scores[1][0] < ambiguity_margin:
        return Resolution(
            ResolutionStatus.AMBIGUOUS,
            None,
            MatchMethod.METADATA,
            top_score,
            "top metadata candidates are too close",
        )
    return Resolution(
        ResolutionStatus.RESOLVED,
        top_candidate,
        MatchMethod.METADATA,
        top_score,
        "metadata match",
    )


def save_resolution(connection: sqlite3.Connection, spotify_id: str, resolution: Resolution) -> None:
    if resolution.status is not ResolutionStatus.RESOLVED or resolution.candidate is None or resolution.method is None:
        raise ValueError("only resolved tracks can be persisted")
    candidate = resolution.candidate
    connection.execute(
        """
        INSERT INTO track_resolutions(
            spotify_id, provider, provider_track_id, match_method, confidence, is_manual,
            candidate_isrc, candidate_title, candidate_artist, candidate_album,
            candidate_duration_ms, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            provider=excluded.provider,
            provider_track_id=excluded.provider_track_id,
            match_method=excluded.match_method,
            confidence=excluded.confidence,
            is_manual=excluded.is_manual,
            candidate_isrc=excluded.candidate_isrc,
            candidate_title=excluded.candidate_title,
            candidate_artist=excluded.candidate_artist,
            candidate_album=excluded.candidate_album,
            candidate_duration_ms=excluded.candidate_duration_ms,
            updated_at=excluded.updated_at
        """,
        (
            spotify_id,
            candidate.provider,
            candidate.provider_track_id,
            resolution.method.value,
            resolution.confidence,
            int(resolution.method is MatchMethod.MANUAL),
            candidate.isrc,
            candidate.title,
            candidate.artist,
            candidate.album,
            candidate.duration_ms,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def set_manual_override(
    connection: sqlite3.Connection,
    spotify_id: str,
    provider: str,
    provider_track_id: str,
) -> None:
    row = connection.execute(
        "SELECT title, artist, album, isrc, duration_ms FROM tracks WHERE spotify_id = ?",
        (spotify_id,),
    ).fetchone()
    if row is None:
        raise KeyError(spotify_id)
    resolution = Resolution(
        ResolutionStatus.RESOLVED,
        Candidate(
            provider=provider,
            provider_track_id=provider_track_id,
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            duration_ms=row["duration_ms"],
        ),
        MatchMethod.MANUAL,
        1.0,
        "manual override",
    )
    save_resolution(connection, spotify_id, resolution)


def get_manual_override(connection: sqlite3.Connection, spotify_id: str) -> ManualOverride | None:
    row = connection.execute(
        "SELECT provider, provider_track_id FROM track_resolutions WHERE spotify_id = ? AND is_manual = 1",
        (spotify_id,),
    ).fetchone()
    if row is None:
        return None
    return ManualOverride(row["provider"], row["provider_track_id"])


def clear_resolution(connection: sqlite3.Connection, spotify_id: str) -> bool:
    cursor = connection.execute("DELETE FROM track_resolutions WHERE spotify_id = ?", (spotify_id,))
    return cursor.rowcount > 0
