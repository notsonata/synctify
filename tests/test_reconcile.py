from __future__ import annotations

from pathlib import Path

import pytest

from synctify.providers.reconcile import (
    find_existing_flac,
    output_reports_existing_file,
    read_flac_candidate,
)
from synctify.resolution import Candidate


def target() -> Candidate:
    return Candidate(
        provider="qobuz",
        provider_track_id="123",
        title="Paranoid Android",
        artist="Radiohead",
        album="OK Computer",
        isrc="GBAYE9701376",
        duration_ms=386_000,
    )


def _metadata_block(block_type: int, payload: bytes, *, last: bool) -> bytes:
    first = block_type | (0x80 if last else 0)
    return bytes([first]) + len(payload).to_bytes(3, "big") + payload


def _streaminfo(duration_ms: int, sample_rate: int = 44_100) -> bytes:
    total_samples = round(duration_ms * sample_rate / 1000)
    channels_minus_one = 1
    bits_minus_one = 15
    packed = (
        (sample_rate << 44)
        | (channels_minus_one << 41)
        | (bits_minus_one << 36)
        | total_samples
    )
    return b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16


def _vorbis_comments(tags: dict[str, list[str]]) -> bytes:
    vendor = b"synctify-test"
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
    tags: dict[str, list[str]],
    *,
    duration_ms: int = 386_000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"fLaC"
    data += _metadata_block(0, _streaminfo(duration_ms), last=False)
    data += _metadata_block(4, _vorbis_comments(tags), last=True)
    path.write_bytes(data)


def _target_tags() -> dict[str, list[str]]:
    return {
        "title": ["Paranoid Android"],
        "artist": ["Radiohead"],
        "album": ["OK Computer"],
        "isrc": ["GBAYE9701376"],
    }


def test_read_flac_candidate_parses_streaminfo_and_vorbis_comments(tmp_path: Path) -> None:
    path = tmp_path / "track.flac"
    _write_flac(path, _target_tags())

    candidate = read_flac_candidate(path)

    assert candidate is not None
    assert candidate.title == "Paranoid Android"
    assert candidate.artist == "Radiohead"
    assert candidate.album == "OK Computer"
    assert candidate.isrc == "GBAYE9701376"
    assert candidate.duration_ms == 386_000


def test_find_existing_flac_uses_tags_and_duration(tmp_path: Path) -> None:
    library = tmp_path / "library"
    match = library / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    other = library / "Other" / "Wrong.flac"
    _write_flac(match, _target_tags())
    _write_flac(
        other,
        {
            "title": ["Different Song"],
            "artist": ["Different Artist"],
            "album": ["Different Album"],
        },
        duration_ms=200_000,
    )

    assert find_existing_flac(target(), library) == match.resolve()


def test_duplicate_equally_good_files_are_ambiguous(tmp_path: Path) -> None:
    library = tmp_path / "library"
    first = library / "a.flac"
    second = library / "b.flac"
    _write_flac(first, _target_tags())
    _write_flac(second, _target_tags())

    assert find_existing_flac(target(), library) is None


def test_untagged_or_malformed_flac_is_ignored(tmp_path: Path) -> None:
    library = tmp_path / "library"
    malformed = library / "bad.flac"
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"not-a-flac")

    assert read_flac_candidate(malformed) is None
    assert find_existing_flac(target(), library) is None


def test_paths_outside_library_are_never_adopted(tmp_path: Path) -> None:
    library = tmp_path / "library"
    outside = tmp_path / "outside.flac"
    library.mkdir()
    _write_flac(outside, _target_tags())

    assert find_existing_flac(target(), library, paths=(outside,)) is None


@pytest.mark.parametrize(
    "message",
    [
        "File already exists",
        "track already downloaded",
        "file exists, skipping",
        "Skipping existing file",
    ],
)
def test_existing_file_messages_are_recognized(message: str) -> None:
    assert output_reports_existing_file(message, "") is True


def test_unrelated_error_is_not_treated_as_existing_file() -> None:
    assert output_reports_existing_file("", "authentication failed") is False
