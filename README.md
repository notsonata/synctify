# Synctify

**Current version: 0.13.0**

Synctify is a local-first macOS music library manager. Spotify defines desired playlist and library state. Synctify resolves those tracks against lossless source services, acquires one canonical local copy, generates UTF-8 M3U8 playlists, mirrors the library to devices, and can maintain a non-destructive cloud backup.

## Architecture

```text
Spotify
   ↓
Desired playlist/library state
   ↓
Automatic source resolution
   Qobuz → Tidal → Deezer → SoundCloud
   ↓
External downloader
   ├── qobuz-dl  → Qobuz
   └── Streamrip → Qobuz / Tidal / Deezer / SoundCloud
   ↓
Canonical local library
   ↓
M3U8 playlists
   ├── mirror → phone / SD card / external drive
   └── backup → pCloud / other rclone remote
```

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify.

Source-service identity and downloader choice are separate concepts. A track can resolve to Tidal while Streamrip performs the transfer, or resolve to Qobuz while qobuz-dl performs the transfer.

## Current capabilities

- Python 3.12+ CLI
- SQLite state database with migrations
- Spotify Authorization Code with PKCE
- OAuth refresh tokens stored in the system keychain
- Liked Songs and owned/collaborative playlist ingestion
- Spotify IDs, ISRCs, metadata, ordering, and diff planning
- deterministic source matching with ambiguity protection
- automatic Qobuz → Tidal → Deezer → SoundCloud fallback
- manual source overrides
- Qobuz acquisition through external qobuz-dl
- Qobuz, Tidal, Deezer, and SoundCloud acquisition through external Streamrip
- resumable acquisition by reconciling existing tagged FLAC files
- SHA-256 persistence for local files
- UTF-8 M3U8 generation
- strict and opt-in partial playlist generation
- mounted-filesystem mirrors through rclone
- deletion propagation for mirror targets
- non-destructive rclone cloud backups
- timestamped playlist backup history
- safe local garbage collection
- macOS GitHub Actions tests

## Installation

Create a virtual environment and install Synctify:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Synctify uses Mutagen to inspect FLAC tags during acquisition reconciliation.

Set `SYNCTIFY_HOME` if you want to override the default application-data directory.

Initialize local state:

```bash
synctify init
synctify status
```

## Spotify setup

Create a Spotify developer application and add this redirect URI:

```text
http://127.0.0.1:8765/callback
```

Authenticate:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
```

Required scopes:

```text
user-library-read
playlist-read-private
playlist-read-collaborative
```

Spotify currently exposes playlist items to Synctify only for playlists the authenticated user owns or collaborates on. Followed non-collaborative playlists owned by somebody else are not imported.

To refresh Spotify state only:

```bash
synctify spotify pull
```

## Coordinated update workflow

The normal update path coordinates the major stages:

```text
Spotify pull
   ↓
automatic source resolution
   ↓
acquire every resolved desired track
   ↓
rebuild M3U8 playlists
```

Preview the complete post-pull state without persisting DB changes or writing files:

```bash
synctify update --dry-run
```

Run the full workflow:

```bash
synctify update
```

Default automatic source priority:

```text
Qobuz → Tidal → Deezer → SoundCloud
```

Fallback is per track. Once a track receives a safe match, later sources are not searched for that track.

Customize source priority:

```bash
synctify update --sources qobuz,tidal
synctify update --sources tidal,qobuz,deezer
```

Use one source only:

```bash
synctify update --source qobuz
synctify update --source tidal
```

`--source` overrides `--sources`.

Other controls:

```bash
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
synctify update --qobuz-dl /path/to/qobuz-dl
synctify update --streamrip /opt/homebrew/bin/rip
```

`--resolution-limit` applies to one frozen set of distinct tracks for the whole fallback chain, not a fresh set per service.

A dry run simulates Spotify changes and safe resolutions inside a SQLite savepoint so downstream planning sees the realistic post-resolution state. It then rolls the savepoint back and performs no downloads or playlist writes.

## Track resolution

Synctify resolves a Spotify track in this order:

1. stored manual override
2. exact normalized ISRC when a candidate exposes an ISRC
3. normalized title, artist, album, and duration scoring
4. unresolved or ambiguous when confidence is insufficient

Automatic metadata matches require a score of at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to choose either candidate automatically.

Inspect state:

```bash
synctify resolve status
```

Run one catalog search explicitly:

```bash
synctify resolve auto --source qobuz --dry-run
synctify resolve auto --source qobuz
synctify resolve auto --source tidal
synctify resolve auto --source deezer
synctify resolve auto --source soundcloud
```

Persist a manual mapping:

```bash
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve set SPOTIFY_TRACK_ID tidal TIDAL_TRACK_ID
```

Clear a stored mapping:

```bash
synctify resolve clear SPOTIFY_TRACK_ID
```

## External downloaders

Synctify does not vendor downloader internals. qobuz-dl and Streamrip remain separately installed applications and are invoked out of process.

### qobuz-dl

Qobuz uses [Sei969/qobuz-dl](https://github.com/Sei969/qobuz-dl) by default.

A separate clone is expected:

```bash
git clone https://github.com/Sei969/qobuz-dl.git
```

Complete its upstream setup, including Qobuz authentication, then put `qobuz-dl` on `PATH`, pass `--qobuz-dl`, or configure `SYNCTIFY_QOBUZ_DL`.

Check availability:

```bash
synctify qobuz doctor
```

### Streamrip

[nathom/streamrip](https://github.com/nathom/streamrip) provides catalog search plus Qobuz, Tidal, Deezer, and SoundCloud download support.

On macOS:

```bash
brew install streamrip
```

Synctify expects `rip` on `PATH`, through `--streamrip`, or through `SYNCTIFY_STREAMRIP`.

Check availability:

```bash
synctify streamrip doctor
```

## Acquisition

Preview resolved tracks waiting for download:

```bash
synctify acquire --dry-run
```

Qobuz defaults to qobuz-dl:

```bash
synctify acquire
synctify acquire --source qobuz
```

Use Streamrip explicitly for Qobuz:

```bash
synctify acquire --source qobuz --downloader streamrip
```

Other supported services use Streamrip by default:

```bash
synctify acquire --source tidal
synctify acquire --source deezer
synctify acquire --source soundcloud
```

### Acquisition reconciliation and resume

External downloaders can decide that a requested track is already present and therefore create no new FLAC. Synctify no longer treats that case as an automatic failure.

When a downloader successfully creates no new FLAC, or explicitly reports a common "already exists" condition, Synctify scans the canonical library and reads existing FLAC tags with Mutagen. It builds local candidates from title, artist, album, ISRC, and duration, then runs the same deterministic matcher used for source resolution.

Synctify adopts an existing file only when that matcher produces one safe unique result. The adopted file is then hashed and persisted to SQLite exactly like a fresh download.

Safety rules:

- reconciliation only considers FLAC files inside the configured canonical library
- untagged or unreadable FLACs are ignored
- exact ISRC is preferred when available
- metadata matches still use the normal `0.88` threshold and `0.04` ambiguity margin
- duplicate equally good files remain ambiguous and are not adopted
- unrelated downloader failures such as authentication errors are still surfaced
- a single-track acquisition that unexpectedly creates multiple new FLACs is still rejected

This keeps resume behavior conservative while avoiding repeated failures when qobuz-dl or Streamrip skips a file that Synctify has not yet recorded.

## Playlist generation

Build playlists:

```bash
synctify playlists build
```

Strict mode writes no playlist when any referenced track is unavailable. To intentionally write partial playlists:

```bash
synctify playlists build --allow-partial
```

M3U8 entries use relative UTF-8 paths into the canonical library. Duplicate Spotify playlist names receive distinct filenames.

## Filesystem mirrors

Add a mounted phone, SD card, USB drive, or external SSD:

```bash
synctify targets add phone /Volumes/Phone/Music
```

Preview destructive mirror behavior first:

```bash
synctify sync phone --dry-run
```

Run the mirror:

```bash
synctify sync phone
```

Destination layout:

```text
/Volumes/Phone/Music/
├── library/
└── playlists/
```

Mirror targets use `rclone sync`, so destination-only files under managed `library/` and `playlists/` roots are removed. Synctify refuses missing/unmounted destinations and paths that overlap its own application-data directory.

## pCloud and rclone backups

Configure pCloud or another rclone backend first:

```bash
rclone config
```

Add a backup target:

```bash
synctify targets add-backup cloud pcloud:Synctify
```

Preview and run:

```bash
synctify backup cloud --dry-run
synctify backup cloud
```

Remote layout:

```text
pcloud:Synctify/
├── library/
└── playlists/
    ├── current/
    └── snapshots/
```

Backup semantics differ from mirror semantics:

- `library/` uses `rclone copy`, so local FLAC deletion does not delete the remote copy
- `playlists/current/` mirrors the current generated playlist set
- `playlists/snapshots/` is append-only history
- unchanged playlists do not create duplicate snapshots
- dry runs do not alter remote data or snapshot history

## Local garbage collection

Preview safe cleanup:

```bash
synctify clean
```

Apply it:

```bash
synctify clean --apply
```

A local track becomes collectible only when it has zero references in current playlist state, including Liked Songs.

Cleanup safety includes protection against paths outside the canonical library, symlink escapes, and shared local paths. Source-service resolutions remain stored so a track can be reacquired if it later returns to desired Spotify state.

## Commands

```text
synctify init
synctify status
synctify spotify login
synctify spotify logout
synctify spotify pull
synctify update
synctify update --dry-run
synctify update --sources qobuz,tidal,deezer,soundcloud
synctify update --source <source>
synctify resolve status
synctify resolve auto --source <source>
synctify resolve set <spotify-id> <source> <source-track-id>
synctify resolve clear <spotify-id>
synctify qobuz doctor
synctify qobuz download-url <url> -d <destination>
synctify streamrip doctor
synctify acquire --source <source>
synctify acquire --dry-run
synctify playlists build
synctify playlists build --allow-partial
synctify targets add <name> <destination>
synctify targets add-backup <name> <remote:path>
synctify targets list
synctify targets remove <name>
synctify sync <target>
synctify sync <target> --dry-run
synctify backup <target>
synctify backup <target> --dry-run
synctify clean
synctify clean --apply
```

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```
