from __future__ import annotations

import os
from pathlib import Path
import re

from .models import Playlist


class MissingLocalTrackError(ValueError):
    pass


def safe_playlist_filename(name: str) -> str:
    cleaned = re.sub(r"[/:]", "_", name).strip()
    return cleaned or "Untitled Playlist"


def render_m3u8(playlist: Playlist, playlist_dir: Path) -> str:
    lines = ["#EXTM3U"]
    for track in playlist.tracks:
        if track.local_path is None:
            raise MissingLocalTrackError(
                f"{track.artist} - {track.title} has no local file"
            )
        relative = os.path.relpath(track.local_path, start=playlist_dir)
        lines.append(Path(relative).as_posix())
    return "\n".join(lines) + "\n"


def write_m3u8(playlist: Playlist, playlist_dir: Path) -> Path:
    playlist_dir.mkdir(parents=True, exist_ok=True)
    output = playlist_dir / f"{safe_playlist_filename(playlist.name)}.m3u8"
    output.write_text(render_m3u8(playlist, playlist_dir), encoding="utf-8", newline="\n")
    return output
