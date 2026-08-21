# Synctify

Synctify is a local-first macOS music library manager. Spotify provides desired playlist/library state, Synctify keeps one canonical local lossless library, generates UTF-8 M3U8 playlists, mirrors that library to devices, and can maintain a non-destructive cloud backup.

## Architecture

```text
Spotify
   ↓
Desired playlist/library state
   ↓
Track resolution
   ↓
Acquisition provider
   ↓
Canonical local library
   ↓
M3U8 playlists
   ├── mirror → phone / external drive
   └── backup → pCloud
```

The acquisition layer is provider-agnostic. Qobuz tooling, existing local files, or another source can implement the same provider interface without making playlist state, matching, or sync logic depend on one downloader.

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify.

## Current state

The project currently includes:

- Python 3.12 package and `synctify` CLI
- SQLite state database with schema migrations
- Spotify Authorization Code with PKCE login
- OAuth refresh tokens stored in the system keychain
- Liked Songs ingestion
- owned and accessible collaborative playlist ingestion
- Spotify track ID and ISRC persistence
- deterministic playlist ordering in SQLite
- change planning for additions, removals, renames, and pure reorders
- deterministic provider-neutral track resolution
- exact ISRC matching before metadata matching
- normalized title/artist/album fallback scoring with duration checks
- ambiguity protection so close candidates are not silently accepted
- persistent automatic and manual provider mappings
- provider-neutral acquisition protocol for future Qobuz/local backends
- UTF-8 M3U8 generation with relative paths
- explicit mirror vs backup sync policy
- rclone command planning
- macOS GitHub Actions tests

A real acquisition provider, actual device transport, playlist generation from imported state, and pCloud snapshots remain future stages.

## Spotify setup

Create a Spotify developer application and add this exact Redirect URI:

```text
http://127.0.0.1:8765/callback
```

Synctify uses Authorization Code with PKCE, so a client secret is not stored in the application.

Then run:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
```

The resulting OAuth token is stored in the system keychain. The non-secret Client ID and redirect URI are stored in Synctify's application-data directory.

You can alternatively provide the Client ID through:

```bash
export SYNCTIFY_SPOTIFY_CLIENT_ID=YOUR_SPOTIFY_CLIENT_ID
synctify spotify login
```

Required scopes are:

```text
user-library-read
playlist-read-private
playlist-read-collaborative
```

### Playlist access limitation

Spotify's current API only exposes playlist items when the authenticated user owns the playlist or is a collaborator. Followed playlists owned by somebody else are therefore not imported into Synctify's desired state.

## Import Spotify state

```bash
synctify spotify pull
```

This imports Liked Songs, accessible playlists, track order, `added_at`, Spotify IDs, ISRCs where available, title, artists, album, and duration.

Inspect changes without updating the database:

```bash
synctify update --dry-run
```

Apply them:

```bash
synctify update
```

At this stage `update` only updates desired Spotify state. It does not yet acquire audio or sync a device.

## Track resolution

Synctify resolves a Spotify track to a provider recording in this order:

1. a stored manual override
2. exact normalized ISRC
3. normalized metadata scoring using title, artist, album, and duration
4. unresolved or ambiguous when confidence is insufficient

An automatic metadata match currently requires a score of at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to auto-select either candidate. Multiple effectively tied exact-ISRC results are also treated as ambiguous.

The resolver itself is active, but no live acquisition provider is connected yet. The next provider implementation can call the resolver with search results and persist only safe matches.

Inspect resolution counts:

```bash
synctify resolve status
```

Persist a manual mapping:

```bash
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
```

Remove it:

```bash
synctify resolve clear SPOTIFY_TRACK_ID
```

## Sync semantics

Synctify treats device synchronization and cloud backup as different operations:

- **Mirror** targets use source-of-truth semantics. Files removed locally are removed from the destination on the next sync. This is intended for phones and external drives.
- **Backup** targets are append/update-only. Remote-only files are never deleted just because they were deleted locally. This is intended for pCloud.
- Playlist backup snapshots will be versioned separately so historical M3U8 states can be retained.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

Initialize local state:

```bash
synctify init
synctify status
```

Set `SYNCTIFY_HOME` to override the default application-data directory during development or testing.

## Commands

Implemented:

```text
synctify init
synctify status
synctify spotify login
synctify spotify logout
synctify spotify pull
synctify update
synctify update --dry-run
synctify resolve status
synctify resolve set <spotify-id> <provider> <provider-track-id>
synctify resolve clear <spotify-id>
```

Planned:

```text
synctify playlists build
synctify sync <target>
synctify backup <target>
synctify clean --dry-run
```
