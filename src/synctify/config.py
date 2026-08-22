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
    spotify_config_path: Path

    @classmethod
    def default(cls) -> "Settings":
        override = os.getenv("SYNCTIFY_HOME")
        home = Path(override).expanduser() if override else user_data_path("Synctify", "notsonata")

        # Import lazily so the low-level path settings module does not create a
        # module-level dependency cycle with persistent user configuration.
        from .user_config import load_user_config, resolve_library_dir

        config = load_user_config(home)
        return cls(
            home=home,
            library_dir=resolve_library_dir(config, home),
            playlists_dir=home / "playlists",
            database_path=home / "synctify.sqlite3",
            spotify_config_path=home / "spotify.json",
        )

    def ensure_directories(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.playlists_dir.mkdir(parents=True, exist_ok=True)
