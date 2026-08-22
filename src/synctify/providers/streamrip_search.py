from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib
from typing import Callable, Sequence

from platformdirs import user_config_dir

from ..models import Track
from ..resolution import Candidate, normalize_isrc
from .streamrip import STREAMRIP_REPOSITORY, STREAMRIP_SOURCES, StreamripUnavailableError


class StreamripSearchError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class StreamripSearchConfig:
    executable: str = "rip"
    extra_args: tuple[str, ...] = ()
    results_per_query: int = 10
    config_path: Path | None = None


class StreamripCatalogSearch:
    """Out-of-process catalog search through Streamrip's sparse JSON output."""

    name = "streamrip"
    supported_sources = STREAMRIP_SOURCES

    def __init__(
        self,
        config: StreamripSearchConfig | None = None,
        *,
        runner: Runner = subprocess.run,
    ) -> None:
        self.config = config or StreamripSearchConfig()
        self._runner = runner

    def supports(self, source: str) -> bool:
        return source.strip().lower() in self.supported_sources

    def is_available(self) -> bool:
        return shutil.which(self.config.executable) is not None

    def require_available(self) -> None:
        if not self.is_available():
            raise StreamripUnavailableError(
                f"{self.config.executable!r} was not found on PATH. Install streamrip first: {STREAMRIP_REPOSITORY}"
            )

    def _streamrip_config_path(self) -> Path:
        if self.config.config_path is not None:
            return self.config.config_path.expanduser()
        return Path(user_config_dir("streamrip")) / "config.toml"

    def require_noninteractive_source_ready(self, source: str) -> None:
        """Refuse provider flows that could launch an interactive login browser."""
        normalized_source = source.strip().lower()
        if normalized_source != "tidal":
            return

        config_path = self._streamrip_config_path()
        try:
            with config_path.open("rb") as handle:
                payload = tomllib.load(handle)
        except FileNotFoundError as exc:
            raise StreamripSearchError(
                "Streamrip Tidal is not configured. Run `rip config --tidal` in Terminal, "
                "complete login once, then retry Synctify."
            ) from exc
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise StreamripSearchError(
                f"could not read Streamrip config {config_path}: {exc}"
            ) from exc

        tidal = payload.get("tidal")
        if not isinstance(tidal, dict):
            raise StreamripSearchError(
                "Streamrip Tidal is not configured. Run `rip config --tidal` in Terminal, "
                "complete login once, then retry Synctify."
            )

        access_token = str(tidal.get("access_token") or "").strip()
        refresh_token = str(tidal.get("refresh_token") or "").strip()
        raw_expiry = tidal.get("token_expiry")
        try:
            token_expiry = float(raw_expiry)
        except (TypeError, ValueError):
            token_expiry = 0.0

        if not access_token or not refresh_token or token_expiry <= time.time() + 60:
            raise StreamripSearchError(
                "Streamrip Tidal login is missing or expired. Run `rip config --tidal` in Terminal "
                "and complete authentication before using Tidal in Synctify. Synctify will not "
                "open provider login pages during automatic resolution."
            )

    def build_search_command(
        self,
        source: str,
        query: str,
        output_file: Path,
        *,
        limit: int,
    ) -> list[str]:
        if limit < 1:
            raise ValueError("search result limit must be at least 1")
        return [
            self.config.executable,
            "--no-db",
            "--no-progress",
            *self.config.extra_args,
            "search",
            "--output-file",
            str(output_file),
            "--num-results",
            str(limit),
            source,
            "track",
            query,
        ]

    @staticmethod
    def _candidate_from_result(source: str, item: object) -> Candidate | None:
        if not isinstance(item, dict):
            return None
        if str(item.get("source", "")).strip().lower() != source:
            return None
        if item.get("media_type") != "track":
            return None
        provider_track_id = str(item.get("id", "")).strip()
        desc = str(item.get("desc", "")).strip()
        if not provider_track_id or " by " not in desc:
            return None
        title, artist = desc.rsplit(" by ", 1)
        title = title.strip()
        artist = artist.strip()
        if not title or not artist:
            return None
        return Candidate(
            provider=source,
            provider_track_id=provider_track_id,
            title=title,
            artist=artist,
        )

    def _search_query(
        self,
        source: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[Candidate, ...]:
        output_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="synctify-streamrip-search-",
                delete=False,
            ) as handle:
                output_path = Path(handle.name)

            result = self._runner(
                self.build_search_command(source, query, output_path, limit=limit),
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "streamrip search failed"
                raise StreamripSearchError(message)

            if not output_path.exists() or output_path.stat().st_size == 0:
                return ()
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise StreamripSearchError(f"invalid Streamrip search JSON: {exc}") from exc
            if not isinstance(payload, list):
                raise StreamripSearchError("Streamrip search output must be a JSON list")

            candidates = [
                candidate
                for item in payload
                if (candidate := self._candidate_from_result(source, item)) is not None
            ]
            return tuple(candidates)
        finally:
            if output_path is not None:
                output_path.unlink(missing_ok=True)

    def search(
        self,
        track: Track,
        source: str,
        *,
        limit: int | None = None,
    ) -> Sequence[Candidate]:
        """Return only candidates supported by two independent search queries.

        Streamrip's machine-readable search output does not expose ISRC, album, or
        duration. A title/artist-only result can otherwise receive a misleadingly
        high metadata score and become a sticky automatic resolution. Synctify
        therefore requires an exact provider track ID to be returned by both an
        ISRC query and an artist/title query. Tracks without Spotify ISRC evidence
        are left unresolved for manual handling rather than guessed automatically.
        """
        normalized_source = source.strip().lower()
        if not self.supports(normalized_source):
            supported = ", ".join(sorted(self.supported_sources))
            raise ValueError(
                f"streamrip search source {source!r} is unsupported; choose one of: {supported}"
            )
        self.require_available()
        self.require_noninteractive_source_ready(normalized_source)
        selected_limit = self.config.results_per_query if limit is None else limit

        normalized_track_isrc = normalize_isrc(track.isrc)
        metadata_query = " ".join(
            part for part in (track.artist.strip(), track.title.strip()) if part
        )
        if not normalized_track_isrc or not metadata_query:
            return ()

        isrc_candidates = self._search_query(
            normalized_source,
            normalized_track_isrc,
            limit=selected_limit,
        )
        metadata_candidates = self._search_query(
            normalized_source,
            metadata_query,
            limit=selected_limit,
        )
        isrc_ids = {
            (candidate.provider, candidate.provider_track_id)
            for candidate in isrc_candidates
        }

        # Preserve metadata-search order while requiring the same catalog identity
        # to have independently appeared in the ISRC results.
        unique: dict[tuple[str, str], Candidate] = {}
        for candidate in metadata_candidates:
            key = (candidate.provider, candidate.provider_track_id)
            if key in isrc_ids:
                unique.setdefault(key, candidate)
        return tuple(unique.values())
