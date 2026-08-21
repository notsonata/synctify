from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from platformdirs import user_data_path


@dataclass(slots=True, frozen=True)
class Settings:
    home: Path
    library_dir: Path
    playlists_dir: Path
    database_path: Path

    @classmethod
    def default(cls) -> "Settings":
        override = os.getenv("SYNCTIFY_HOME")
        home = Path(override).expanduser() if override else user_data_path("Synctify", "notsonata")
        return cls(
            home=home,
            library_dir=home / "library",
            playlists_dir=home / "playlists",
            database_path=home / "synctify.sqlite3",
        )

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.playlists_dir.mkdir(parents=True, exist_ok=True)
