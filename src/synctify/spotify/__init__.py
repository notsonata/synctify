"""Spotify authentication and desired-state ingestion."""

from .ingest import SpotifySnapshot, fetch_spotify_snapshot

__all__ = ["SpotifySnapshot", "fetch_spotify_snapshot"]
