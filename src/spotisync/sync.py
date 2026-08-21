from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SyncMode(StrEnum):
    MIRROR = "mirror"
    BACKUP = "backup"


@dataclass(slots=True, frozen=True)
class SyncTarget:
    name: str
    destination: str
    mode: SyncMode


def rclone_command(source: Path, target: SyncTarget) -> list[str]:
    operation = "sync" if target.mode is SyncMode.MIRROR else "copy"
    return [
        "rclone",
        operation,
        str(source),
        target.destination,
        "--create-empty-src-dirs",
    ]


def destination_deletes_enabled(mode: SyncMode) -> bool:
    """Return whether source deletions should propagate to the destination."""
    return mode is SyncMode.MIRROR
