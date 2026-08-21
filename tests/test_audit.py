from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from synctify.acquisition import file_sha256
from synctify.audit import AuditIssueKind, audit_library
from synctify.cli_entry import app
from synctify.db import connect, initialize
from synctify.resolution import set_manual_override


def _metadata_block(block_type: int, payload: bytes, *, last: bool) -> bytes:
    first = block_type | (0x80 if last else 0)
    return bytes([first]) + len(payload).to_bytes(3, "big") + payload


def _streaminfo(duration_ms: int, sample_rate: int = 44_100) -> bytes:
    total_samples = round(duration_ms * sample_rate / 1000)
    packed = (sample_rate << 44) | (1 << 41) | (15 << 36) | total_samples
    return b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16


def _vorbis_comments(tags: dict[str, list[str]]) -> bytes:
    vendor = b"synctify-audit-test"
    comments = [
        f"{key.upper()}={value}".encode("utf-8")
        for key, values in tags.items()
        for value in values
    ]
    payload = len(vendor).to_bytes(4, "little") + vendor
    payload += len(comments).to_bytes(4, "little")
    for comment in comments:
        payload += len(comment).to_bytes(4, "little") + comment
    return payload


def _write_flac(
    path: Path,
    *,
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    album: str = "OK Computer",
    isrc: str = "GBAYE9701376",
    duration_ms: int = 386_000,
) -> None:
    tags = {
        "title": [title],
        "artist": [artist],
        "album": [album],
        "isrc": [isrc],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"fLaC"
    data += _metadata_block(0, _streaminfo(duration_ms), last=False)
    data += _metadata_block(4, _vorbis_comments(tags), last=True)
    path.write_bytes(data)


def _insert_track(
    connection,
    *,
    spotify_id: str = "spotify-1",
    title: str = "Paranoid Android",
    artist: str = "Radiohead",
    album: str = "OK Computer",
    isrc: str = "GBAYE9701376",
    duration_ms: int = 386_000,
    local_path: Path | None = None,
    sha256: str | None = None,
    referenced: bool = True,
    resolution: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO tracks(
            spotify_id, title, artist, album, isrc, duration_ms, local_path, sha256, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spotify_id,
            title,
            artist,
            album,
            isrc,
            duration_ms,
            str(local_path) if local_path is not None else None,
            sha256,
            "local" if local_path is not None else "unresolved",
        ),
    )
    if resolution:
        set_manual_override(connection, spotify_id, "qobuz", f"qobuz-{spotify_id}")
    if referenced:
        connection.execute(
            """
            INSERT OR IGNORE INTO playlists(
                spotify_id, name, source_kind, collaborative
            ) VALUES ('playlist-1', 'Playlist', 'playlist', 0)
            """
        )
        position = connection.execute(
            "SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = 'playlist-1'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO playlist_tracks(playlist_id, track_id, position) VALUES ('playlist-1', ?, ?)",
            (spotify_id, position),
        )


def test_audit_preview_reports_missing_without_mutating_state(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    missing = library / "Radiohead" / "missing.flac"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, local_path=missing, sha256="deadbeef")
        connection.commit()
        report = audit_library(connection, library)
        row = connection.execute(
            "SELECT local_path, sha256 FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert report.missing == 1
    assert report.repairs == ()
    assert row["local_path"] == str(missing)
    assert row["sha256"] == "deadbeef"


def test_repair_relinks_missing_track_to_unique_untracked_flac(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    missing = library / "old" / "missing.flac"
    replacement = library / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    _write_flac(replacement)
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, local_path=missing, sha256="oldhash")
        connection.commit()
        report = audit_library(connection, library, repair=True)
        row = connection.execute(
            "SELECT local_path, sha256, status, qobuz_id FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert len(report.proposed_adoptions) == 1
    assert any(item.action == "relinked" for item in report.repairs)
    assert row["local_path"] == str(replacement.resolve())
    assert row["sha256"] == file_sha256(replacement)
    assert row["status"] == "local"
    assert row["qobuz_id"] == "qobuz-spotify-1"


def test_repair_clears_missing_state_when_no_safe_replacement_exists(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    missing = library / "missing.flac"
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, local_path=missing, sha256="oldhash")
        connection.commit()
        report = audit_library(connection, library, repair=True)
        row = connection.execute(
            "SELECT local_path, sha256, status FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert any(item.action == "cleared-missing" for item in report.repairs)
    assert row["local_path"] is None
    assert row["sha256"] is None
    assert row["status"] == "resolved"


def test_hash_mismatch_is_updated_only_when_metadata_still_matches(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    good = library / "good.flac"
    bad = library / "bad.flac"
    _write_flac(good)
    _write_flac(
        bad,
        title="Different Song",
        artist="Different Artist",
        album="Different Album",
        isrc="USXXX0000001",
        duration_ms=200_000,
    )
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, spotify_id="good", local_path=good, sha256="wrong")
        _insert_track(
            connection,
            spotify_id="bad",
            local_path=bad,
            sha256="wrong",
            referenced=False,
        )
        connection.commit()
        report = audit_library(connection, library, repair=True)
        good_row = connection.execute(
            "SELECT sha256 FROM tracks WHERE spotify_id = 'good'"
        ).fetchone()
        bad_row = connection.execute(
            "SELECT sha256 FROM tracks WHERE spotify_id = 'bad'"
        ).fetchone()

    assert report.hash_problems == 2
    assert good_row["sha256"] == file_sha256(good)
    assert bad_row["sha256"] == "wrong"
    assert [item.action for item in report.repairs].count("hash-updated") == 1


def test_one_untracked_file_is_not_assigned_to_two_tracks(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    only_file = library / "track.flac"
    _write_flac(only_file)
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, spotify_id="spotify-a", local_path=None)
        _insert_track(connection, spotify_id="spotify-b", local_path=None)
        connection.commit()
        report = audit_library(connection, library, repair=True)
        local_count = connection.execute(
            "SELECT COUNT(*) FROM tracks WHERE local_path IS NOT NULL"
        ).fetchone()[0]

    assert len(report.untracked_files) == 1
    assert report.proposed_adoptions == ()
    assert local_count == 0


def test_outside_recorded_path_is_reported_but_never_rewritten(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    library = tmp_path / "library"
    outside = tmp_path / "outside.flac"
    _write_flac(outside)
    initialize(database)

    with connect(database) as connection:
        _insert_track(connection, local_path=outside, sha256=file_sha256(outside))
        connection.commit()
        report = audit_library(connection, library, repair=True)
        row = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()

    assert any(issue.kind is AuditIssueKind.OUTSIDE_LIBRARY for issue in report.issues)
    assert row["local_path"] == str(outside)
    assert report.repairs == ()


def test_cli_audit_is_preview_only_by_default(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCTIFY_HOME", str(home))
    initialize(home / "synctify.sqlite3")

    with connect(home / "synctify.sqlite3") as connection:
        _insert_track(connection, local_path=home / "library" / "missing.flac", sha256="old")

    result = CliRunner().invoke(app, ["audit"])

    assert result.exit_code == 0, result.output
    assert "Library audit" in result.output
    assert "Preview only" in result.output
    with connect(home / "synctify.sqlite3") as connection:
        row = connection.execute(
            "SELECT local_path FROM tracks WHERE spotify_id = 'spotify-1'"
        ).fetchone()
    assert row["local_path"] is not None
