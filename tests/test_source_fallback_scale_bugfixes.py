from __future__ import annotations

from synctify.auto_resolution import (
    AutoResolutionAttempt,
    AutoResolutionReport,
    pending_resolution_tracks,
)
from synctify.models import Track
from synctify.resolution import Candidate, MatchMethod, Resolution, ResolutionStatus
from synctify.spotify.state import ChangePlan
from synctify.workflow import UpdateWorkflowReport


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _NoParameterizedIdConnection:
    def execute(self, sql: str, params=()):
        assert params == (), "Spotify ID filtering must not bind one SQL variable per ID"
        return _Rows(
            [
                {
                    "spotify_id": "wanted",
                    "title": "Song",
                    "artist": "Artist",
                    "album": "Album",
                    "isrc": "ISRC1",
                    "duration_ms": 180_000,
                    "local_path": None,
                },
                {
                    "spotify_id": "other",
                    "title": "Other",
                    "artist": "Artist",
                    "album": "Album",
                    "isrc": "ISRC2",
                    "duration_ms": 181_000,
                    "local_path": None,
                },
            ]
        )


def _plan() -> ChangePlan:
    return ChangePlan((), (), (), (), 0, 0, 0, ())


def test_selected_fallback_ids_are_filtered_without_sql_variable_binding() -> None:
    # Include a deliberately large frozen selection to guard against restoring an
    # IN (?, ?, ...) query in a later refactor.
    selected = tuple(["wanted", *(f"missing-{index}" for index in range(50_000))])

    tracks = pending_resolution_tracks(
        _NoParameterizedIdConnection(),  # type: ignore[arg-type]
        spotify_ids=selected,
    )

    assert [track.spotify_id for track in tracks] == ["wanted"]


def test_compatibility_resolution_uses_final_fallback_outcome() -> None:
    track = Track(
        spotify_id="spotify-1",
        title="Song",
        artist="Artist",
        album="Album",
        isrc="ISRC1",
        duration_ms=180_000,
    )
    first = AutoResolutionAttempt(
        track=track,
        candidate_count=0,
        resolution=Resolution(
            ResolutionStatus.UNRESOLVED,
            None,
            None,
            0.0,
            "no candidates",
        ),
    )
    candidate = Candidate(
        provider="tidal",
        provider_track_id="tidal-1",
        title="Song",
        artist="Artist",
        album="Album",
        isrc="ISRC1",
        duration_ms=180_000,
    )
    second = AutoResolutionAttempt(
        track=track,
        candidate_count=1,
        resolution=Resolution(
            ResolutionStatus.RESOLVED,
            candidate,
            MatchMethod.ISRC,
            1.0,
            "exact ISRC",
        ),
    )
    report = UpdateWorkflowReport(
        spotify=_plan(),
        resolution_sources=("qobuz", "tidal"),
        resolutions=(
            AutoResolutionReport("qobuz", (first,), False),
            AutoResolutionReport("tidal", (second,), False),
        ),
        acquisitions=(),
        playlist_readiness=None,
        playlists=None,
        dry_run=False,
    )

    compatibility = report.resolution

    assert compatibility.source == "qobuz -> tidal"
    assert compatibility.resolved == 1
    assert compatibility.unresolved == 0
    assert compatibility.failed == 0
    assert compatibility.attempts == (second,)


def test_compatibility_resolution_keeps_last_unrecovered_error() -> None:
    track = Track("spotify-1", "Song", "Artist")
    unresolved = AutoResolutionAttempt(
        track=track,
        candidate_count=0,
        resolution=Resolution(
            ResolutionStatus.UNRESOLVED,
            None,
            None,
            0.0,
            "no candidates",
        ),
    )
    failed = AutoResolutionAttempt(
        track=track,
        candidate_count=0,
        resolution=None,
        error="catalog unavailable",
    )
    report = UpdateWorkflowReport(
        spotify=_plan(),
        resolution_sources=("qobuz", "tidal"),
        resolutions=(
            AutoResolutionReport("qobuz", (unresolved,), False),
            AutoResolutionReport("tidal", (failed,), False),
        ),
        acquisitions=(),
        playlist_readiness=None,
        playlists=None,
        dry_run=False,
    )

    compatibility = report.resolution

    assert compatibility.failed == 1
    assert compatibility.unresolved == 0
    assert compatibility.attempts == (failed,)
