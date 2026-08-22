from __future__ import annotations

import json
from pathlib import Path

import pytest

from synctify.migration_checkpoint import (
    STAGE_PORTABLE_IMPORT,
    MigrationCheckpointError,
    checkpoint_from_dict,
    checkpoint_path,
    checkpoint_to_dict,
    load_checkpoint,
    mark_stage_completed,
    migration_identity,
    new_checkpoint,
    refresh_complete,
    save_checkpoint,
)


def _portable(path: Path, value: str = "qobuz") -> None:
    path.write_text(json.dumps({"value": value}), encoding="utf-8")


def test_checkpoint_round_trip_is_versioned_and_atomic(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    source = tmp_path / "library"
    source.mkdir()
    _portable(portable)
    identity = migration_identity(
        portable,
        library_source=source,
        relink_limit=25,
        allow_partial=True,
    )
    checkpoint = new_checkpoint(identity)
    mark_stage_completed(
        checkpoint,
        STAGE_PORTABLE_IMPORT,
        summary={"config_keys": 2},
    )
    path = checkpoint_path(tmp_path / "home")

    save_checkpoint(path, checkpoint)
    loaded = load_checkpoint(path)

    assert loaded is not None
    assert loaded.fingerprint == identity.fingerprint
    assert loaded.identity.library_source == str(source.resolve())
    assert loaded.identity.relink_limit == 25
    assert loaded.identity.allow_partial is True
    assert loaded.stages[STAGE_PORTABLE_IMPORT].summary == {"config_keys": 2}
    assert not path.with_name(f".{path.name}.tmp").exists()


def test_identity_changes_when_portable_contents_change(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable, "qobuz")
    first = migration_identity(
        portable,
        library_source=None,
        relink_limit=None,
        allow_partial=False,
    )

    _portable(portable, "tidal")
    second = migration_identity(
        portable,
        library_source=None,
        relink_limit=None,
        allow_partial=False,
    )

    assert first.portable_sha256 != second.portable_sha256
    assert first.fingerprint != second.fingerprint


def test_identity_changes_for_resume_relevant_options(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    source = tmp_path / "library"
    other_source = tmp_path / "other-library"
    source.mkdir()
    other_source.mkdir()
    _portable(portable)

    base = migration_identity(
        portable,
        library_source=source,
        relink_limit=None,
        allow_partial=False,
    )
    limited = migration_identity(
        portable,
        library_source=source,
        relink_limit=10,
        allow_partial=False,
    )
    partial = migration_identity(
        portable,
        library_source=source,
        relink_limit=None,
        allow_partial=True,
    )
    moved = migration_identity(
        portable,
        library_source=other_source,
        relink_limit=None,
        allow_partial=False,
    )

    assert len({base.fingerprint, limited.fingerprint, partial.fingerprint, moved.fingerprint}) == 4


def test_checkpoint_rejects_unknown_stage(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable)
    checkpoint = new_checkpoint(
        migration_identity(
            portable,
            library_source=None,
            relink_limit=None,
            allow_partial=False,
        )
    )
    raw = checkpoint_to_dict(checkpoint)
    raw["stages"] = {
        "unknown-stage": {
            "completed_at": "2026-08-22T00:00:00+00:00",
            "operational_failures": 0,
            "summary": {},
        }
    }

    with pytest.raises(MigrationCheckpointError, match="unknown migration stage"):
        checkpoint_from_dict(raw)


def test_complete_flag_requires_all_selected_stages(tmp_path: Path) -> None:
    portable = tmp_path / "portable.json"
    _portable(portable)
    checkpoint = new_checkpoint(
        migration_identity(
            portable,
            library_source=None,
            relink_limit=None,
            allow_partial=False,
        )
    )
    mark_stage_completed(checkpoint, STAGE_PORTABLE_IMPORT)

    assert refresh_complete(checkpoint, has_library=False, spotify_login=False) is False
    assert checkpoint.complete is False
