# Synctify

**Current version: 0.14.0**

Synctify is a local-first macOS music library manager. Spotify defines desired playlist/library state. Synctify resolves those tracks against lossless source services, acquires one canonical local copy, generates UTF-8 M3U8 playlists, mirrors the library to devices, and can maintain a non-destructive cloud backup.

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

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify. Source-service identity and downloader choice are separate concepts.

## Current capabilities

- Python 3.12+ CLI
- SQLite state database with migrations
- Spotify PKCE authentication with refresh tokens in the system keychain
- Liked Songs and owned/collaborative playlist ingestion
- deterministic source matching with ambiguity protection
- automatic Qobuz → Tidal → Deezer → SoundCloud fallback
- manual source overrides
- Qobuz acquisition through external qobuz-dl
- Qobuz, Tidal, Deezer, and SoundCloud acquisition through external Streamrip
- resumable acquisition by reconciling existing tagged FLAC files
- SHA-256 persistence for local files
- library audit/repair for missing files, hash drift, and untracked FLAC reconciliation
- UTF-8 M3U8 generation
- strict and opt-in partial playlist generation
- mounted-filesystem mirrors through rclone
- non-destructive rclone cloud backups with playlist snapshots
- safe local garbage collection
- macOS GitHub Actions tests

## Installation

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
synctify init
```

Set `SYNCTIFY_HOME` to override the default application-data directory.

## Spotify setup

Create a Spotify developer application and add:

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

Refresh Spotify state only:

```bash
synctify spotify pull
```

## Coordinated update

Preview the full post-pull workflow without persistent DB or filesystem changes:

```bash
synctify update --dry-run
```

Run it:

```bash
synctify update
```

Default automatic source priority:

```text
Qobuz → Tidal → Deezer → SoundCloud
```

Fallback is per track. Once a track receives a safe match, later sources are not searched for that track.

Customize priority:

```bash
synctify update --sources qobuz,tidal
synctify update --sources tidal,qobuz,deezer
```

Use one source only:

```bash
synctify update --source qobuz
```

Useful controls:

```bash
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
synctify update --qobuz-dl /path/to/qobuz-dl
synctify update --streamrip /opt/homebrew/bin/rip
```

`--resolution-limit` applies to one frozen set of distinct tracks across the whole fallback chain.

## Track resolution

Resolution order:

1. stored manual override
2. exact normalized ISRC when available
3. normalized title, artist, album, and duration scoring
4. unresolved or ambiguous when confidence is insufficient

Automatic metadata matches require at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to choose automatically.

```bash
synctify resolve status
synctify resolve auto --source qobuz --dry-run
synctify resolve auto --source qobuz
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve clear SPOTIFY_TRACK_ID
```

## External downloaders

Synctify does not vendor downloader internals. Both downloaders remain separate applications invoked out of process.

### qobuz-dl

Qobuz defaults to [Sei969/qobuz-dl](https://github.com/Sei969/qobuz-dl).

```bash
git clone https://github.com/Sei969/qobuz-dl.git
synctify qobuz doctor
```

Complete qobuz-dl's upstream authentication/setup separately. Put the executable on `PATH`, pass `--qobuz-dl`, or use `SYNCTIFY_QOBUZ_DL`.

### Streamrip

[nathom/streamrip](https://github.com/nathom/streamrip) is used for catalog search and for Tidal, Deezer, SoundCloud, and optional Qobuz acquisition.

```bash
brew install streamrip
synctify streamrip doctor
```

Put `rip` on `PATH`, pass `--streamrip`, or use `SYNCTIFY_STREAMRIP`.

## Acquisition

```bash
synctify acquire --dry-run
synctify acquire --source qobuz
synctify acquire --source tidal
synctify acquire --source deezer
synctify acquire --source soundcloud
```

Qobuz uses qobuz-dl by default. The other source services use Streamrip by default. Qobuz can explicitly use Streamrip with `--downloader streamrip`.

### Resume and reconciliation

A downloader may skip a requested track because a FLAC already exists, even when Synctify has not recorded that file yet. Synctify can reconcile that state instead of requiring every acquisition to create exactly one new file.

When a downloader creates no new FLAC, or reports a recognized "already exists" condition, Synctify scans the canonical library and reads FLAC STREAMINFO plus Vorbis comments with a small read-only parser implemented in Synctify itself.

The existing deterministic matcher then compares title, artist, album, ISRC, and duration. Synctify adopts the file only when there is one safe unique match.

Safety rules:

- only FLAC files inside the canonical library are considered
- unreadable or untagged files are ignored
- exact ISRC is preferred when available
- metadata matching retains the `0.88` threshold and `0.04` ambiguity margin
- duplicate equally good files remain ambiguous
- unrelated downloader failures still surface normally
- a single-track acquisition that unexpectedly creates multiple new FLACs is still rejected

A reconciled file is hashed and persisted to SQLite exactly like a fresh download.

## Library audit and repair

Audit the canonical library without changing anything:

```bash
synctify audit
```

Apply only safe database repairs:

```bash
synctify audit --repair
```

The audit checks:

- recorded local paths that no longer exist
- recorded paths outside the canonical library
- paths that are not regular files
- missing stored SHA-256 values
- local files whose current SHA-256 differs from SQLite
- FLAC files inside the canonical library that are not referenced by any `tracks.local_path`

For desired tracks with no usable recorded local file, Synctify compares untracked FLAC metadata against the Spotify track using the same deterministic matcher used elsewhere. A file is relinked only when the match is safe, unique, and one-to-one. If one untracked file could satisfy multiple tracks, Synctify refuses to assign it automatically.

`--repair` may:

- relink a desired track to one safely matched untracked FLAC
- calculate and persist the FLAC SHA-256
- refresh a mismatched/missing stored hash only when the existing file's FLAC metadata still matches that track
- clear stale local path/hash state for a recorded file that is genuinely missing and has no safe replacement, allowing normal acquisition to resume

`audit --repair` never deletes audio. Unsafe outside-library paths and ambiguous/unreadable files are reported but left unchanged.

## Playlist generation

```bash
synctify playlists build
synctify playlists build --allow-partial
```

Strict mode skips an incomplete playlist. Partial mode writes only currently available tracks.

## Filesystem mirrors

```bash
synctify targets add phone /Volumes/Phone/Music
synctify sync phone --dry-run
synctify sync phone
```

Mirror targets use `rclone sync`, so destination-only files under managed `library/` and `playlists/` roots are removed. Missing/unmounted destinations and unsafe overlapping paths are refused.

## Cloud backup

Configure pCloud or another rclone backend, then:

```bash
synctify targets add-backup cloud pcloud:Synctify
synctify backup cloud --dry-run
synctify backup cloud
```

Backup semantics:

- `library/` uses `rclone copy`, so local deletion does not remove the remote audio copy
- `playlists/current/` mirrors the latest generated set
- `playlists/snapshots/` keeps append-only history
- unchanged playlists do not create duplicate snapshots

## Local garbage collection

```bash
synctify clean
synctify clean --apply
```

A file becomes collectible only when its track has zero current playlist references. Paths outside the canonical library, symlink escapes, and shared local paths are protected. Source resolutions remain so a track can be reacquired if it returns to desired Spotify state.

## Development

```bash
python -m pytest
```

Version consistency tests require `pyproject.toml`, `synctify.__version__`, and the README version above to remain synchronized.
