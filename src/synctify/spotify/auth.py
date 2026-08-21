from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPES = ("user-library-read", "playlist-read-private", "playlist-read-collaborative")
KEYRING_SERVICE = "synctify.spotify"


class SpotifyAuthError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SpotifyOAuthConfig:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @classmethod
    def load(cls, path: Path) -> "SpotifyOAuthConfig":
        if not path.exists():
            raise SpotifyAuthError("Spotify is not configured. Run: synctify spotify login --client-id <client-id>")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            client_id = data["client_id"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SpotifyAuthError(f"Invalid Spotify configuration at {path}: {exc}") from exc
        if not isinstance(client_id, str) or not client_id:
            raise SpotifyAuthError(f"Invalid Spotify Client ID in {path}.")
        redirect_uri = data.get("redirect_uri", DEFAULT_REDIRECT_URI)
        if not isinstance(redirect_uri, str):
            raise SpotifyAuthError(f"Invalid Spotify redirect URI in {path}.")
        return cls(client_id=client_id, redirect_uri=redirect_uri)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)


@dataclass(slots=True, frozen=True)
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_at: float

    def is_expiring(self, *, skew_seconds: int = 30) -> bool:
        return time.time() + skew_seconds >= self.expires_at


class KeyringTokenStore:
    def load(self, client_id: str) -> OAuthToken | None:
        try:
            import keyring
            raw = keyring.get_password(KEYRING_SERVICE, client_id)
        except Exception as exc:
            raise SpotifyAuthError(f"Could not read Spotify token from the system keychain: {exc}") from exc
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return OAuthToken(data["access_token"], data["refresh_token"], float(data["expires_at"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise SpotifyAuthError("Stored Spotify token is invalid. Run: synctify spotify login") from exc

    def save(self, client_id: str, token: OAuthToken) -> None:
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, client_id, json.dumps(asdict(token)))
        except Exception as exc:
            raise SpotifyAuthError(f"Could not save Spotify token to the system keychain: {exc}") from exc

    def clear(self, client_id: str) -> None:
        try:
            import keyring
            try:
                keyring.delete_password(KEYRING_SERVICE, client_id)
            except keyring.errors.PasswordDeleteError:
                pass
        except Exception as exc:
            raise SpotifyAuthError(f"Could not update the system keychain: {exc}") from exc


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(config: SpotifyOAuthConfig, verifier: str, state: str) -> str:
    query = urlencode({"client_id": config.client_id, "response_type": "code", "redirect_uri": config.redirect_uri, "scope": " ".join(SCOPES), "code_challenge_method": "S256", "code_challenge": code_challenge(verifier), "state": state})
    return f"{AUTH_URL}?{query}"


def _token_from_response(data: dict[str, object], *, previous_refresh_token: str | None = None) -> OAuthToken:
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token") or previous_refresh_token
    expires_in = data.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise SpotifyAuthError("Spotify token response did not include the expected tokens.")
    if not isinstance(expires_in, (int, float)):
        raise SpotifyAuthError("Spotify token response did not include a valid expiry.")
    return OAuthToken(access_token, refresh_token, time.time() + float(expires_in))


def _validate_redirect_uri(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise SpotifyAuthError(f"Local Spotify login currently requires an explicit loopback redirect such as {DEFAULT_REDIRECT_URI}.")
    return parsed.hostname, parsed.port, parsed.path or "/"


def interactive_login(config: SpotifyOAuthConfig, store: KeyringTokenStore, *, timeout_seconds: int = 180, browser_open: Callable[[str], object] = webbrowser.open, http_client: httpx.Client | None = None) -> OAuthToken:
    host, port, callback_path = _validate_redirect_uri(config.redirect_uri)
    verifier = generate_code_verifier()
    expected_state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            result["state"] = params.get("state", [""])[0]
            result["code"] = params.get("code", [""])[0]
            result["error"] = params.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Synctify Spotify login complete.</h2><p>You can close this tab and return to the terminal.</p></body></html>")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer((host, port), CallbackHandler)
    server.timeout = timeout_seconds
    try:
        browser_open(authorization_url(config, verifier, expected_state))
        server.handle_request()
    finally:
        server.server_close()

    if not result:
        raise SpotifyAuthError("Spotify login timed out before a callback was received.")
    if result.get("state") != expected_state:
        raise SpotifyAuthError("Spotify login returned an invalid OAuth state value.")
    if result.get("error"):
        raise SpotifyAuthError(f"Spotify authorization failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise SpotifyAuthError("Spotify login did not return an authorization code.")

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=30)
    try:
        response = client.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code, "redirect_uri": config.redirect_uri, "client_id": config.client_id, "code_verifier": verifier})
        response.raise_for_status()
        token = _token_from_response(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise SpotifyAuthError(f"Could not exchange Spotify authorization code: {exc}") from exc
    finally:
        if owns_client:
            client.close()
    store.save(config.client_id, token)
    return token


class SpotifyAuth:
    def __init__(self, config: SpotifyOAuthConfig, store: KeyringTokenStore | None = None, http_client: httpx.Client | None = None) -> None:
        self.config = config
        self.store = store or KeyringTokenStore()
        self._client = http_client

    def access_token(self, *, force_refresh: bool = False) -> str:
        token = self.store.load(self.config.client_id)
        if token is None:
            raise SpotifyAuthError("Spotify is not logged in. Run: synctify spotify login")
        if force_refresh or token.is_expiring():
            token = self._refresh(token)
        return token.access_token

    def _refresh(self, token: OAuthToken) -> OAuthToken:
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": self.config.client_id})
            response.raise_for_status()
            refreshed = _token_from_response(response.json(), previous_refresh_token=token.refresh_token)
        except (httpx.HTTPError, ValueError) as exc:
            raise SpotifyAuthError(f"Could not refresh Spotify access token: {exc}") from exc
        finally:
            if owns_client:
                client.close()
        self.store.save(self.config.client_id, refreshed)
        return refreshed


def resolve_config(path: Path, *, client_id: str | None = None, redirect_uri: str | None = None) -> SpotifyOAuthConfig:
    existing = SpotifyOAuthConfig.load(path) if path.exists() else None
    resolved_client_id = client_id or os.getenv("SYNCTIFY_SPOTIFY_CLIENT_ID") or (existing.client_id if existing else None)
    if not resolved_client_id:
        raise SpotifyAuthError("A Spotify Client ID is required. Pass --client-id or set SYNCTIFY_SPOTIFY_CLIENT_ID.")
    config = SpotifyOAuthConfig(resolved_client_id, redirect_uri or (existing.redirect_uri if existing else DEFAULT_REDIRECT_URI))
    _validate_redirect_uri(config.redirect_uri)
    config.save(path)
    return config
