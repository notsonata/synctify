from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from synctify.providers.reconcile import find_existing_flac, output_reports_existing_file
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


def _fake_flac(tags_by_path: dict[Path, dict[str, list[str]]], lengths: dict[Path, float]):
    def factory(path: Path):
        resolved = Path(path).resolve()
        return SimpleNamespace(
            tags=tags_by_path[resolved],
            info=SimpleNamespace(length=lengths[resolved]),
        )

    return factory


def test_find_existing_flac_uses_tags_and_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    match = library / "Radiohead" / "OK Computer" / "06 - Paranoid Android.flac"
    other = library / "Other" / "Wrong.flac"
    match.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    match.write_bytes(b"match")
    other.write_bytes(b"other")

    tags = {
        match.resolve(): {
            "title": ["Paranoid Android"],
            "artist": ["Radiohead"],
            "album": ["OK Computer"],
            "isrc": ["GBAYE9701376"],
        },
        other.resolve(): {
            "title": ["Different Song"],
            "artist": ["Different Artist"],
            "album": ["Different Album"],
        },
    }
    lengths = {match.resolve(): 386.0, other.resolve(): 200.0}
    monkeypatch.setattr(
        "synctify.providers.reconcile.FLAC",
        _fake_flac(tags, lengths),
    )

    assert find_existing_flac(target(), library) == match.resolve()


def test_duplicate_equally_good_files_are_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    first = library / "a.flac"
    second = library / "b.flac"
    library.mkdir()
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    metadata = {
        "title": ["Paranoid Android"],
        "artist": ["Radiohead"],
        "album": ["OK Computer"],
        "isrc": ["GBAYE9701376"],
    }
    tags = {first.resolve(): metadata, second.resolve(): metadata}
    lengths = {first.resolve(): 386.0, second.resolve(): 386.0}
    monkeypatch.setattr(
        "synctify.providers.reconcile.FLAC",
        _fake_flac(tags, lengths),
    )

    assert find_existing_flac(target(), library) is None


def test_paths_outside_library_are_never_adopted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    outside = tmp_path / "outside.flac"
    library.mkdir()
    outside.write_bytes(b"outside")

    tags = {
        outside.resolve(): {
            "title": ["Paranoid Android"],
            "artist": ["Radiohead"],
            "album": ["OK Computer"],
            "isrc": ["GBAYE9701376"],
        }
    }
    lengths = {outside.resolve(): 386.0}
    monkeypatch.setattr(
        "synctify.providers.reconcile.FLAC",
        _fake_flac(tags, lengths),
    )

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
