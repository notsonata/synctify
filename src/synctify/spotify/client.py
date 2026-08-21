from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from .auth import SpotifyAuth

API_BASE = "https://api.spotify.com/v1"


class SpotifyAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Spotify API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class SpotifyClient:
    def __init__(self, auth: SpotifyAuth, *, http_client: httpx.Client | None = None, sleep: Any = time.sleep) -> None:
        self.auth = auth
        self._client = http_client or httpx.Client(timeout=30)
        self._owns_client = http_client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SpotifyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_json(self, path_or_url: str, *, params: dict[str, object] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
        refreshed = False
        force_refresh = False
        rate_retries = 0
        while True:
            token = self.auth.access_token(force_refresh=force_refresh)
            force_refresh = False
            response = self._client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
            params = None
            if response.status_code == 401 and not refreshed:
                refreshed = True
                force_refresh = True
                continue
            if response.status_code == 429 and rate_retries < 3:
                rate_retries += 1
                try:
                    seconds = max(0.0, min(float(response.headers.get("Retry-After", "1")), 60.0))
                except ValueError:
                    seconds = 1.0
                self._sleep(seconds)
                continue
            if response.is_error:
                try:
                    payload = response.json()
                    error = payload.get("error", payload)
                    message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                except ValueError:
                    message = response.text or response.reason_phrase
                raise SpotifyAPIError(response.status_code, message)
            try:
                return response.json()
            except ValueError as exc:
                raise SpotifyAPIError(response.status_code, "invalid JSON response") from exc

    def _items(self, path: str, *, params: dict[str, object] | None = None) -> Iterator[dict[str, Any]]:
        next_url: str | None = path
        next_params = params
        while next_url:
            page = self._get_json(next_url, params=next_params)
            for item in page.get("items", []):
                if isinstance(item, dict):
                    yield item
            raw_next = page.get("next")
            next_url = raw_next if isinstance(raw_next, str) and raw_next else None
            next_params = None

    def me(self) -> dict[str, Any]:
        return self._get_json("/me")

    def saved_tracks(self) -> Iterator[dict[str, Any]]:
        return self._items("/me/tracks", params={"limit": 50})

    def current_user_playlists(self) -> Iterator[dict[str, Any]]:
        return self._items("/me/playlists", params={"limit": 50})

    def playlist_items(self, playlist_id: str) -> Iterator[dict[str, Any]]:
        return self._items(f"/playlists/{playlist_id}/items", params={"limit": 50, "additional_types": "track"})
