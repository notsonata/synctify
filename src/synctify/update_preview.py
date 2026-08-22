from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator

from .db import connect, initialize


@contextmanager
def preview_database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a disposable current-schema database for update previews.

    Existing state is copied with SQLite's backup API so WAL-backed databases are
    cloned consistently. The persistent database is opened read-only and is never
    migrated. Missing persistent state starts from a fresh temporary schema.
    """
    with tempfile.TemporaryDirectory(prefix="synctify-update-preview-") as directory:
        preview_path = Path(directory) / "synctify.sqlite3"
        if database_path.exists():
            if not database_path.is_file():
                raise ValueError(f"Synctify database path is not a file: {database_path}")
            source_uri = f"{database_path.resolve().as_uri()}?mode=ro"
            try:
                source = sqlite3.connect(source_uri, uri=True)
                destination = sqlite3.connect(preview_path)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"could not clone Synctify database for preview: {exc}") from exc

        # Initialize/migrate only the disposable clone. This keeps a legacy
        # persistent database untouched while letting preview code use current tables.
        initialize(preview_path)
        with connect(preview_path) as connection:
            yield connection
