# Synctify

**Current version: 0.6.0**

Synctify is a local-first macOS music library manager. Spotify provides desired playlist/library state, Synctify keeps one canonical local lossless library, generates UTF-8 M3U8 playlists, mirrors that library to devices, and can maintain a non-destructive cloud backup.

## Architecture

```text
Spotify
   ↓
Desired playlist/library state
   ↓
Track resolution
   ↓
External downloader
   ├── qobuz-dl
   └── streamrip
   ↓
Canonical local library
   ↓
M3U8 playlists
   ├── mirror → phone / external drive
   └── backup → pCloud
```

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify. Downloaders remain external tools and can be swapped without changing Synctify's local library or playlist model.

## Current state

Synctify currently includes:

- Python 3.12 package and `synctify` CLI
- SQLite state database with schema migrations
- Spotify Authorization Code with PKCE login
- OAuth refresh tokens stored in the system keychain
- Liked Songs and accessible playlist ingestion
- Spotify IDs, ISRCs, metadata, ordering, and change planning
- deterministic provider-neutral track resolution
- exact ISRC matching before metadata matching
- ambiguity protection and persistent manual overrides
- external qobuz-dl and streamrip acquisition adapters
- downloaded FLAC path and SHA-256 persistence
- database-backed UTF-8 M3U8 playlist generation
- strict and opt-in partial playlist handling
- explicit mirror vs backup sync policy
- macOS GitHub Actions tests

Device transport, pCloud snapshots, and garbage collection remain future stages.

## Spotify setup

Create a Spotify developer application and add this Redirect URI:

```text
http://127.0.0.1:8765/callback
```

Then run:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
```

The OAuth token is stored in the system keychain. The non-secret Client ID and redirect URI are stored in Synctify's application-data directory.

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
synctify update --dry-run
synctify update
```

`update` refreshes desired Spotify state. Acquisition remains a separate explicit step.

## Track resolution

Synctify resolves a Spotify track to a provider recording in this order:

1. stored manual override
2. exact normalized ISRC
3. normalized title, artist, album, and duration scoring
4. unresolved or ambiguous when confidence is insufficient

An automatic metadata match requires a score of at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to auto-select either candidate.

Inspect resolution counts:

```bash
synctify resolve status
```

Persist a manual Qobuz mapping:

```bash
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
```

Remove it:

```bash
synctify resolve clear SPOTIFY_TRACK_ID
```

## External downloaders

Synctify can use either [Sei969/qobuz-dl](https://github.com/Sei969/qobuz-dl) or [nathom/streamrip](https://github.com/nathom/streamrip) for Qobuz downloads. Synctify does not vendor or copy either project.

### qobuz-dl

A separate clone is required:

```bash
git clone https://github.com/Sei969/qobuz-dl.git
```

Complete qobuz-dl's upstream setup inside that clone, including Qobuz authentication. The resulting `qobuz-dl` executable must be on `PATH`, supplied with `--qobuz-dl`, or configured with `SYNCTIFY_QOBUZ_DL`.

Check it:

```bash
synctify qobuz doctor
synctify qobuz doctor --executable /path/to/qobuz-dl/.venv/bin/qobuz-dl
```

### streamrip

Streamrip is optional and can be installed separately using its upstream instructions. On macOS it supports Homebrew:

```bash
brew install streamrip
```

It can also be installed from its repository or Python package. Synctify expects the `rip` executable on `PATH`, through `--streamrip`, or through `SYNCTIFY_STREAMRIP`.

Check it:

```bash
synctify streamrip doctor
```

Both integrations remain out-of-process. A track resolved to Qobuz is still stored as a Qobuz resolution regardless of which downloader transfers the FLAC.

## Acquisition

Inspect pending resolved downloads:

```bash
synctify acquire --dry-run
```

qobuz-dl remains the default:

```bash
synctify acquire
synctify acquire --downloader qobuz-dl
```

Use Streamrip instead:

```bash
synctify acquire --downloader streamrip
```

Useful options:

```bash
synctify acquire --limit 10
synctify acquire --downloader qobuz-dl --quality 27
synctify acquire --downloader streamrip --quality 4
synctify acquire --qobuz-dl /path/to/qobuz-dl/.venv/bin/qobuz-dl
synctify acquire --downloader streamrip --streamrip /opt/homebrew/bin/rip
```

For Synctify-managed acquisitions, each downloader's downloaded-ID database is bypassed where supported. Synctify records the resulting FLAC path, Qobuz track ID, SHA-256 hash, and local status in its own SQLite state database.

A direct qobuz-dl passthrough remains available for diagnostics:

```bash
synctify qobuz download-url https://open.qobuz.com/track/123456789 -d /tmp/qobuz-test
```

## Playlist generation

Build M3U8 files directly from imported Spotify playlist order and acquired local paths:

```bash
synctify playlists build
```

The default is strict. If any track in a playlist has not been acquired, Synctify reports the missing tracks and does not write that playlist. This avoids silently producing a playlist that looks complete but is not.

To intentionally create partial playlists from the files currently available:

```bash
synctify playlists build --allow-partial
```

M3U8 entries use relative UTF-8 paths into the canonical library. Duplicate Spotify playlist names receive distinct filenames so they do not overwrite each other.

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
python -m pytest
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
synctify qobuz doctor
synctify qobuz download-url <url> -d <destination>
synctify streamrip doctor
synctify acquire
synctify acquire --dry-run
synctify playlists build
synctify playlists build --allow-partial
```

Planned:

```text
synctify sync <target>
synctify backup <target>
synctify clean --dry-run
```
