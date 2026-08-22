from __future__ import annotations

from pathlib import Path

from synctify.acquisition import pending_acquisitions
from synctify.audit import AuditIssueKind, audit_library
from synctify.db import connect, initialize
from synctify.relink import relink_library


def _resolved_desired_track(
    connection,
    spotify_id: str,
    *,
    local_path: str | None,
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    album: str = "OK Computer",
    isrc: str = "GBAYE9701376",
    duration_ms: int = 386_000,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(
            spotify_id, isrc, title, artist, album, duration_ms,
            local_path, sha256, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'stale-hash', 'local')
        """,
        (
            spotify_id,
            isrc,
            title,
            artist,
            album,
            duration_ms,
            local_path,
        ),
    )
    connection.execute(
        """
        INSERT INTO track_resolutions(
            spotify_id, provider, provider_track_id, match_method, confidence,
            is_manual, updated_at
        ) VALUES (?, 'qobuz', ?, 'manual', 1.0, 1, '2026-08-22T00:00:00+00:00')
        """,
        (spotify_id, f"qobuz-{spotify_id}"),
    )
    playlist_id = f"playlist-{spotify_id}"
    connection.execute(
        "INSERT INTO playlists(spotify_id, name) VALUES (?, ?)",
        (playlist_id, playlist_id),
    )
    connection.execute(
        "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES (?, ?, 0)",
        (playlist_id, spotify_id),
    )


def _metadata_block(block_type: int, payload: bytes, *, last: bool) -> bytes:
    first = block_type | (0x80 if last else 0)
    return bytes([first]) + len(payload).to_bytes(3, "big") + payload


def _streaminfo(duration_ms: int, sample_rate: int = 44_100) -> bytes:
    total_samples = round(duration_ms * sample_rate / 1000)
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    return b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16


def _vorbis_comments(tags: dict[str, str]) -> bytes:
    vendor = b"synctify-local-state-test"
    comments = [f"{key.upper()}={value}".encode("utf-8") for key, value in tags.items()]
    payload = len(vendor).to_bytes(4, "little") + vendor
    payload += len(comments).to_bytes(4, "little")
    for comment in comments:
        payload += len(comment).to_bytes(4, "little") + comment
    return payload


def _write_flac(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"fLaC"
    data += _metadata_block(0, _streaminfo(386_000), last=False)
    data += _metadata_block(
        4,
        _vorbis_comments(
            {
                "title": "Paranoid Android",
                "artist": "Radiohead",
                "album": "OK Computer",
                "isrc": "GBAYE9701376",
            }
        ),
        last=True,
    )
    path.write_bytes(data)


def test_pending_acquisitions_requeues_missing_recorded_file(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    missing = tmp_path / "library" / "missing.flac"
    initialize(db)

    with connect(db) as connection:
        _resolved_desired_track(connection, "spotify-1", local_path=str(missing))
        tasks = pending_acquisitions(connection)

    assert [task.spotify_id for task in tasks] == ["spotify-1"]


def test_pending_acquisitions_requeues_directory_recorded_as_file(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    wrong = tmp_path / "library" / "not-a-file.flac"
    wrong.mkdir(parents=True)
    initialize(db)

    with connect(db) as connection:
        _resolved_desired_track(connection, "spotify-1", local_path=str(wrong))
        tasks = pending_acquisitions(connection)

    assert [task.spotify_id for task in tasks] == ["spotify-1"]


def test_audit_repair_clears_non_file_local_state_for_reacquisition(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    wrong = library / "not-a-file.flac"
    wrong.mkdir(parents=True)
    initialize(db)

    with connect(db) as connection:
        _resolved_desired_track(connection, "spotify-1", local_path=str(wrong))
        report = audit_library(connection, library, repair=True)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert any(issue.kind is AuditIssueKind.NOT_FILE for issue in report.issues)
    assert any(repair.action == "cleared-not-file" for repair in report.repairs)
    assert row["local_path"] is None
    assert row["sha256"] is None
    assert row["status"] == "resolved"


def test_relink_limit_still_reserves_source_against_future_batch(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    library = tmp_path / "canonical"
    source = tmp_path / "copied"
    _write_flac(source / "song.flac")
    initialize(db)

    with connect(db) as connection:
        _resolved_desired_track(connection, "spotify-1", local_path=None)
        _resolved_desired_track(connection, "spotify-2", local_path=None)
        report = relink_library(connection, source, library, limit=1, apply=True)
        rows = connection.execute(
            "SELECT spotify_id, local_path FROM tracks ORDER BY spotify_id"
        ).fetchall()

    assert report.target_tracks == 1
    assert report.matches == ()
    assert len(report.unmatched) == 1
    assert "multiple desired tracks" in report.unmatched[0].reason
    assert all(row["local_path"] is None for row in rows)
    assert not library.exists()
