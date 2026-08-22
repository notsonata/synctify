from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


CHECKPOINT_FORMAT = "synctify-migration-checkpoint"
CHECKPOINT_VERSION = 1
CHECKPOINT_FILENAME = "migration-checkpoint.json"

STAGE_PORTABLE_IMPORT = "portable-import"
STAGE_SPOTIFY_LOGIN = "spotify-login"
STAGE_SPOTIFY_REFRESH = "spotify-refresh"
STAGE_LIBRARY_RELINK = "library-relink"
STAGE_PLAYLISTS = "playlists"
STAGE_AUDIT = "audit"
STAGE_DOCTOR = "doctor"

STAGE_ORDER = (
    STAGE_PORTABLE_IMPORT,
    STAGE_SPOTIFY_LOGIN,
    STAGE_SPOTIFY_REFRESH,
    STAGE_LIBRARY_RELINK,
    STAGE_PLAYLISTS,
    STAGE_AUDIT,
    STAGE_DOCTOR,
)
_STAGE_SET = frozenset(STAGE_ORDER)


class MigrationCheckpointError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class MigrationIdentity:
    portable_sha256: str
    library_source: str | None
    relink_limit: int | None
    allow_partial: bool

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "allow_partial": self.allow_partial,
                "library_source": self.library_source,
                "portable_sha256": self.portable_sha256,
                "relink_limit": self.relink_limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True, frozen=True)
class MigrationStageRecord:
    completed_at: str
    operational_failures: int = 0
    summary: dict[str, int | str] = field(default_factory=dict)


@dataclass(slots=True)
class MigrationCheckpoint:
    identity: MigrationIdentity
    stages: dict[str, MigrationStageRecord] = field(default_factory=dict)
    complete: bool = False
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    last_error_stage: str | None = None
    last_error: str | None = None

    @property
    def fingerprint(self) -> str:
        return self.identity.fingerprint

    def is_completed(self, stage: str) -> bool:
        return stage in self.stages

    def completed_stages(self) -> tuple[str, ...]:
        return tuple(stage for stage in STAGE_ORDER if stage in self.stages)

    def completed_failures(self, stages: tuple[str, ...] | None = None) -> int:
        selected = stages if stages is not None else self.completed_stages()
        return sum(self.stages[stage].operational_failures for stage in selected if stage in self.stages)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_path(home: Path) -> Path:
    return home / CHECKPOINT_FILENAME


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def migration_identity(
    portable_file: Path,
    *,
    library_source: Path | None,
    relink_limit: int | None,
    allow_partial: bool,
) -> MigrationIdentity:
    portable = portable_file.expanduser().resolve()
    if not portable.is_file():
        raise MigrationCheckpointError(f"portable migration file is not readable: {portable}")
    try:
        portable_sha = _sha256_file(portable)
    except OSError as exc:
        raise MigrationCheckpointError(f"could not hash portable migration file: {exc}") from exc

    source_value: str | None = None
    if library_source is not None:
        try:
            source_value = str(library_source.expanduser().resolve())
        except OSError as exc:
            raise MigrationCheckpointError(f"could not resolve library source: {exc}") from exc

    return MigrationIdentity(
        portable_sha256=portable_sha,
        library_source=source_value,
        relink_limit=relink_limit,
        allow_partial=allow_partial,
    )


def _stage_to_dict(record: MigrationStageRecord) -> dict[str, object]:
    return {
        "completed_at": record.completed_at,
        "operational_failures": record.operational_failures,
        "summary": dict(sorted(record.summary.items())),
    }


def checkpoint_to_dict(checkpoint: MigrationCheckpoint) -> dict[str, object]:
    return {
        "format": CHECKPOINT_FORMAT,
        "format_version": CHECKPOINT_VERSION,
        "fingerprint": checkpoint.fingerprint,
        "inputs": {
            "portable_sha256": checkpoint.identity.portable_sha256,
            "library_source": checkpoint.identity.library_source,
            "relink_limit": checkpoint.identity.relink_limit,
            "allow_partial": checkpoint.identity.allow_partial,
        },
        "stages": {
            stage: _stage_to_dict(checkpoint.stages[stage])
            for stage in STAGE_ORDER
            if stage in checkpoint.stages
        },
        "complete": checkpoint.complete,
        "created_at": checkpoint.created_at,
        "updated_at": checkpoint.updated_at,
        "last_error_stage": checkpoint.last_error_stage,
        "last_error": checkpoint.last_error,
    }


def _parse_summary(raw: object) -> dict[str, int | str]:
    if not isinstance(raw, dict):
        raise MigrationCheckpointError("checkpoint stage summary must be an object")
    parsed: dict[str, int | str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, (int, str)) or isinstance(value, bool):
            raise MigrationCheckpointError("checkpoint stage summary values must be strings or integers")
        parsed[key] = value
    return parsed


def _parse_stage(stage: str, raw: object) -> MigrationStageRecord:
    if stage not in _STAGE_SET:
        raise MigrationCheckpointError(f"checkpoint contains unknown migration stage: {stage}")
    if not isinstance(raw, dict) or set(raw) != {"completed_at", "operational_failures", "summary"}:
        raise MigrationCheckpointError(f"invalid checkpoint record for stage {stage}")
    completed_at = raw.get("completed_at")
    failures = raw.get("operational_failures")
    if not isinstance(completed_at, str) or not completed_at:
        raise MigrationCheckpointError(f"invalid completion time for stage {stage}")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        raise MigrationCheckpointError(f"invalid operational failure count for stage {stage}")
    return MigrationStageRecord(completed_at, failures, _parse_summary(raw.get("summary")))


def checkpoint_from_dict(raw: object) -> MigrationCheckpoint:
    if not isinstance(raw, dict):
        raise MigrationCheckpointError("migration checkpoint must be a JSON object")
    allowed = {
        "format",
        "format_version",
        "fingerprint",
        "inputs",
        "stages",
        "complete",
        "created_at",
        "updated_at",
        "last_error_stage",
        "last_error",
    }
    if set(raw) != allowed:
        raise MigrationCheckpointError("migration checkpoint contains unexpected fields")
    if raw.get("format") != CHECKPOINT_FORMAT or raw.get("format_version") != CHECKPOINT_VERSION:
        raise MigrationCheckpointError("unsupported migration checkpoint format")

    inputs = raw.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "portable_sha256",
        "library_source",
        "relink_limit",
        "allow_partial",
    }:
        raise MigrationCheckpointError("invalid migration checkpoint inputs")
    portable_sha = inputs.get("portable_sha256")
    library_source = inputs.get("library_source")
    relink_limit = inputs.get("relink_limit")
    allow_partial = inputs.get("allow_partial")
    if not isinstance(portable_sha, str) or len(portable_sha) != 64:
        raise MigrationCheckpointError("invalid checkpoint portable SHA-256")
    if library_source is not None and not isinstance(library_source, str):
        raise MigrationCheckpointError("invalid checkpoint library source")
    if relink_limit is not None and (
        not isinstance(relink_limit, int) or isinstance(relink_limit, bool) or relink_limit < 1
    ):
        raise MigrationCheckpointError("invalid checkpoint relink limit")
    if not isinstance(allow_partial, bool):
        raise MigrationCheckpointError("invalid checkpoint allow_partial value")

    identity = MigrationIdentity(portable_sha, library_source, relink_limit, allow_partial)
    fingerprint = raw.get("fingerprint")
    if fingerprint != identity.fingerprint:
        raise MigrationCheckpointError("migration checkpoint fingerprint does not match its inputs")

    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, dict):
        raise MigrationCheckpointError("checkpoint stages must be an object")
    stages = {stage: _parse_stage(stage, value) for stage, value in stages_raw.items()}

    complete = raw.get("complete")
    created_at = raw.get("created_at")
    updated_at = raw.get("updated_at")
    last_error_stage = raw.get("last_error_stage")
    last_error = raw.get("last_error")
    if not isinstance(complete, bool):
        raise MigrationCheckpointError("invalid checkpoint complete flag")
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise MigrationCheckpointError("invalid checkpoint timestamps")
    if last_error_stage is not None and (
        not isinstance(last_error_stage, str) or last_error_stage not in _STAGE_SET
    ):
        raise MigrationCheckpointError("invalid checkpoint last error stage")
    if last_error is not None and not isinstance(last_error, str):
        raise MigrationCheckpointError("invalid checkpoint last error")

    return MigrationCheckpoint(
        identity=identity,
        stages=stages,
        complete=complete,
        created_at=created_at,
        updated_at=updated_at,
        last_error_stage=last_error_stage,
        last_error=last_error,
    )


def load_checkpoint(path: Path) -> MigrationCheckpoint | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise MigrationCheckpointError(f"migration checkpoint path is not a file: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationCheckpointError(f"could not read migration checkpoint: {exc}") from exc
    return checkpoint_from_dict(raw)


def save_checkpoint(path: Path, checkpoint: MigrationCheckpoint) -> None:
    checkpoint.updated_at = _now()
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(checkpoint_to_dict(checkpoint), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MigrationCheckpointError(f"could not write migration checkpoint: {exc}") from exc


def new_checkpoint(identity: MigrationIdentity) -> MigrationCheckpoint:
    return MigrationCheckpoint(identity=identity)


def mark_stage_completed(
    checkpoint: MigrationCheckpoint,
    stage: str,
    *,
    operational_failures: int = 0,
    summary: Mapping[str, int | str] | None = None,
) -> None:
    if stage not in _STAGE_SET:
        raise MigrationCheckpointError(f"unknown migration stage: {stage}")
    if operational_failures < 0:
        raise MigrationCheckpointError("operational_failures cannot be negative")
    checkpoint.stages[stage] = MigrationStageRecord(
        completed_at=_now(),
        operational_failures=operational_failures,
        summary=dict(summary or {}),
    )
    if checkpoint.last_error_stage == stage:
        checkpoint.last_error_stage = None
        checkpoint.last_error = None


def record_stage_error(checkpoint: MigrationCheckpoint, stage: str, error: str) -> None:
    if stage not in _STAGE_SET:
        raise MigrationCheckpointError(f"unknown migration stage: {stage}")
    checkpoint.last_error_stage = stage
    checkpoint.last_error = error


def required_stages(*, has_library: bool, spotify_login: bool) -> tuple[str, ...]:
    stages = [STAGE_PORTABLE_IMPORT]
    if spotify_login:
        stages.append(STAGE_SPOTIFY_LOGIN)
    stages.append(STAGE_SPOTIFY_REFRESH)
    if has_library:
        stages.append(STAGE_LIBRARY_RELINK)
    stages.extend((STAGE_PLAYLISTS, STAGE_AUDIT, STAGE_DOCTOR))
    return tuple(stages)


def refresh_complete(
    checkpoint: MigrationCheckpoint,
    *,
    has_library: bool,
    spotify_login: bool,
) -> bool:
    required = required_stages(has_library=has_library, spotify_login=spotify_login)
    checkpoint.complete = all(stage in checkpoint.stages for stage in required)
    return checkpoint.complete
