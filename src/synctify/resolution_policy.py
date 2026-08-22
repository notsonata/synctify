from __future__ import annotations

import sqlite3
from typing import Iterable

SUPPORTED_RESOLUTION_PROVIDERS = frozenset({"qobuz", "tidal", "deezer"})


def normalize_resolution_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if not normalized:
        raise ValueError("provider cannot be empty")
    if normalized not in SUPPORTED_RESOLUTION_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_RESOLUTION_PROVIDERS))
        raise ValueError(
            f"unsupported resolution provider {provider!r}; supported: {supported}"
        )
    return normalized


def clear_unsupported_resolutions(
    connection: sqlite3.Connection,
    *,
    supported: Iterable[str] = SUPPORTED_RESOLUTION_PROVIDERS,
) -> int:
    """Remove stale resolution rows that cannot be acquired by current Synctify.

    Local audio state is intentionally left intact. If the file later goes missing,
    the track can be resolved again through a supported source instead of remaining
    stuck behind an obsolete provider row.
    """
    normalized = tuple(sorted({item.strip().lower() for item in supported if item.strip()}))
    if not normalized:
        raise ValueError("at least one supported resolution provider is required")
    placeholders = ", ".join("?" for _ in normalized)
    cursor = connection.execute(
        f"DELETE FROM track_resolutions WHERE provider NOT IN ({placeholders})",
        normalized,
    )
    return cursor.rowcount
