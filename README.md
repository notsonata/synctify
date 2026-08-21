# Synctify

**Current version: 0.11.0**

Synctify is a local-first macOS music library manager. Spotify provides desired playlist/library state, Synctify keeps one canonical local lossless library, generates UTF-8 M3U8 playlists, mirrors that library to devices, and can maintain a non-destructive cloud backup.

## Architecture

```text
Spotify
   ↓
Desired playlist/library state
   ↓
Source-service resolution
   ├── Qobuz
   ├── Tidal
   ├── Deezer
   └── SoundCloud
   ↓
External downloader
   ├── qobuz-dl  → Qobuz
   └── Streamrip → Qobuz / Tidal / Deezer / SoundCloud
   ↓
Canonical local library
   ↓
M3U8 playlists
   ├── mirror → phone / external drive
   └── backup → pCloud / rclone remote
```

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify. Source-service identity and downloader choice are separate concepts: a track can be resolved to Tidal while Streamrip performs the transfer, or resolved to Qobuz while qobuz-dl performs the transfer.

## Current state

Synctify currently includes:

- Python 3.12 package and `synctify` CLI
- SQLite state database with schema migrations
- Spotify Authorization Code with PKCE login
- OAuth refresh tokens stored in the system keychain
- Liked Songs and accessible playlist ingestion
- Spotify IDs, ISRCs, metadata, ordering, and change planning
- coordinated `update` workflow for Spotify refresh, resolution, acquisition, and playlist rebuild
- transactional `update --dry-run` simulation with no persistent DB or filesystem changes
- deterministic source-service track resolution
- automatic catalog lookup through external Streamrip
- Qobuz, Tidal, Deezer, and SoundCloud automatic metadata matching
- exact ISRC matching when candidate metadata provides an ISRC
- ambiguity protection and persistent manual overrides
- Qobuz acquisition through external qobuz-dl
- Qobuz, Tidal, Deezer, and SoundCloud acquisition through external Streamrip
- downloaded FLAC path and SHA-256 persistence
- database-backed UTF-8 M3U8 playlist generation
- strict and opt-in partial playlist handling
- mounted-filesystem mirror targets using rclone
- deletion propagation for device mirrors
- non-destructive rclone cloud backups
- timestamped playlist backup history
- safe local garbage collection for tracks with zero current playlist references
- macOS GitHub Actions tests

Richer source metadata enrichment and additional catalog-search adapters can be added later without changing the downloader boundary.

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

## Coordinated update workflow

The normal update path coordinates the major stages in order:

```text
Spotify pull
   ↓
automatic source resolution
   ↓
acquire every currently resolved desired track
   ↓
rebuild M3U8 playlists
```

Preview the complete post-pull state without persisting anything:

```bash
synctify update --dry-run
```

The dry run fetches Spotify, applies the new desired state inside a SQLite savepoint, simulates automatic resolutions, calculates the resulting download plan, checks current playlist readiness, then rolls the savepoint back. It does not download audio and does not write M3U8 files.

Run the full workflow:

```bash
synctify update
```

Qobuz is the default catalog used for automatic resolution:

```bash
synctify update --source qobuz
```

You can choose another Streamrip-supported catalog for the automatic-resolution stage:

```bash
synctify update --source tidal
synctify update --source deezer
synctify update --source soundcloud
```

The selected `--source` controls only which catalog is searched for tracks that do not yet have a stored resolution. The acquisition stage then processes all pending desired tracks that already have resolutions, regardless of source. Qobuz resolutions use qobuz-dl by default, while Tidal, Deezer, and SoundCloud resolutions use Streamrip.

Useful update controls:

```bash
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
synctify update --qobuz-dl /path/to/qobuz-dl/.venv/bin/qobuz-dl
synctify update --streamrip /opt/homebrew/bin/rip
```

Successful stages are committed as the workflow progresses. If one download fails, Synctify preserves earlier successful Spotify/resolution/download work, continues reporting the remaining state, and still attempts to rebuild playlists from the files that are safely available.

All lower-level commands remain available when you want manual control over a particular stage.

## Import Spotify state only

To refresh Spotify desired state without running acquisition or playlist rebuilding, use:

```bash
synctify spotify pull
```

## Track resolution

Synctify resolves a Spotify track to a source-service recording in this order:

1. stored manual override
2. exact normalized ISRC when the source candidate exposes one
3. normalized title, artist, album, and duration scoring using the metadata available
4. unresolved or ambiguous when confidence is insufficient

An automatic metadata match requires a score of at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to auto-select either candidate.

Inspect resolution counts:

```bash
synctify resolve status
```

### Automatic catalog resolution

Synctify can use the separately installed Streamrip CLI as an out-of-process catalog-search adapter. Streamrip supports Qobuz, Tidal, Deezer, and SoundCloud search. qobuz-dl remains the default downloader for resolved Qobuz tracks.

Preview automatic Qobuz matching without saving anything:

```bash
synctify resolve auto --source qobuz --dry-run
```

Persist safe matches:

```bash
synctify resolve auto --source qobuz
```

The same search path is available for Streamrip's other supported catalogs:

```bash
synctify resolve auto --source tidal
synctify resolve auto --source deezer
synctify resolve auto --source soundcloud
```

Useful controls:

```bash
synctify resolve auto --source qobuz --limit 25
synctify resolve auto --source qobuz --search-results 10
synctify resolve auto --source qobuz --streamrip /opt/homebrew/bin/rip
```

For a track with an ISRC, Synctify first asks Streamrip to search that ISRC and also performs a title/artist query. Results are deduplicated before the deterministic matcher runs. Streamrip's JSON search output currently exposes the source ID plus a title/artist description, so Synctify does not pretend that those candidates contain richer metadata such as ISRC, album, or duration when it is not present. This makes automatic matching conservative rather than inventing certainty.

Existing automatic and manual mappings are not overwritten by `resolve auto`. Clear a mapping first if you intentionally want it reconsidered.

Persist manual source mappings:

```bash
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve set SPOTIFY_TRACK_ID tidal TIDAL_TRACK_ID
synctify resolve set SPOTIFY_TRACK_ID deezer DEEZER_TRACK_ID
synctify resolve set SPOTIFY_TRACK_ID soundcloud SOUNDCLOUD_TRACK_ID
```

Remove one:

```bash
synctify resolve clear SPOTIFY_TRACK_ID
```

## External downloaders

Synctify does not vendor or copy downloader internals. External tools remain separately installed applications.

### qobuz-dl: Qobuz-specific default

Synctify uses [Sei969/qobuz-dl](https://github.com/Sei969/qobuz-dl) as the default downloader for Qobuz.

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

### Streamrip: multi-service search and download adapter

[nathom/streamrip](https://github.com/nathom/streamrip) supports Qobuz, Tidal, Deezer, and SoundCloud. Synctify uses its scriptable catalog search for automatic resolution, and uses Streamrip as the downloader for non-Qobuz sources. qobuz-dl remains the Qobuz-specific download default.

On macOS Streamrip can be installed with Homebrew:

```bash
brew install streamrip
```

Synctify expects the `rip` executable on `PATH`, through `--streamrip`, or through `SYNCTIFY_STREAMRIP`.

Check it:

```bash
synctify streamrip doctor
```

## Acquisition

Inspect pending Qobuz downloads:

```bash
synctify acquire --dry-run
```

Qobuz defaults to qobuz-dl:

```bash
synctify acquire
synctify acquire --source qobuz
```

You can explicitly use Streamrip for Qobuz if needed:

```bash
synctify acquire --source qobuz --downloader streamrip
```

For Streamrip's other supported source services, Streamrip is selected automatically:

```bash
synctify acquire --source tidal
synctify acquire --source deezer
synctify acquire --source soundcloud
```

Useful options:

```bash
synctify acquire --source qobuz --limit 10
synctify acquire --source qobuz --quality 27
synctify acquire --source tidal --quality 3
synctify acquire --source deezer --quality 2
synctify acquire --qobuz-dl /path/to/qobuz-dl/.venv/bin/qobuz-dl
synctify acquire --source tidal --streamrip /opt/homebrew/bin/rip
```

Streamrip acquisitions use its exact-ID JSON input, so Synctify passes the stored source service, media type, and source track ID directly rather than constructing guessed URLs.

For Synctify-managed acquisitions, each downloader's downloaded-ID database is bypassed where supported. Synctify records the resulting FLAC path, SHA-256 hash, local status, and the source-service resolution in its own SQLite state database.

A direct qobuz-dl passthrough remains available for diagnostics:

```bash
synctify qobuz download-url https://open.qobuz.com/track/123456789 -d /tmp/qobuz-test
```

## Playlist generation

Build M3U8 files directly from imported Spotify playlist order and acquired local paths:

```bash
synctify playlists build
```

The default is strict. If any track in a playlist has not been acquired, Synctify reports the missing tracks and does not write that playlist.

To intentionally create partial playlists from the files currently available:

```bash
synctify playlists build --allow-partial
```

M3U8 entries use relative UTF-8 paths into the canonical library. Duplicate Spotify playlist names receive distinct filenames so they do not overwrite each other.

## Filesystem device mirrors

Filesystem mirror targets are intended for mounted phones, SD cards, USB drives, and external SSDs. Synctify uses `rclone sync`, so local deletion propagates to the destination on the next mirror.

Install rclone separately and make sure `rclone` is on `PATH`.

Add a target:

```bash
synctify targets add phone /Volumes/Phone/Music
```

List targets:

```bash
synctify targets list
```

Always inspect a destructive mirror first when configuring a new target:

```bash
synctify sync phone --dry-run
```

Then run the mirror:

```bash
synctify sync phone
```

The destination layout is:

```text
/Volumes/Phone/Music/
├── library/
└── playlists/
```

This mirrors Synctify's local sibling layout so relative M3U8 paths remain valid on the target.

Safety behavior:

- the destination root must already exist, which prevents an unmounted volume path from being silently created on the Mac
- Synctify refuses destinations that overlap its own application-data directory
- mirror mode deletes destination-only files under `library/` and `playlists/`
- a failed library sync stops before the playlist sync begins
- dry runs are not recorded as completed sync runs

Remove only the target configuration, without deleting files from the device:

```bash
synctify targets remove phone
```

## pCloud and rclone backups

Cloud backups use an rclone remote. pCloud is the intended backend, but the backup layer only requires an rclone remote path.

Configure pCloud with rclone first:

```bash
rclone config
```

For example, if the configured remote is named `pcloud`, add it to Synctify like this:

```bash
synctify targets add-backup cloud pcloud:Synctify
```

Preview the backup:

```bash
synctify backup cloud --dry-run
```

Run it:

```bash
synctify backup cloud
```

The remote layout is:

```text
pcloud:Synctify/
├── library/
└── playlists/
    ├── current/
    └── snapshots/
        ├── Driving/
        │   ├── 2026-08-21T150000Z.m3u8
        │   └── 2026-08-24T183211Z.m3u8
        └── Liked Songs/
            └── 2026-08-21T150000Z.m3u8
```

Backup semantics are intentionally different from device mirroring:

- `library/` uses `rclone copy`, so deleting a local FLAC does not delete its remote backup
- a playlist snapshot is uploaded only when that local M3U8 changed since its last successful snapshot for that target
- `playlists/snapshots/` is never synchronized or deleted by Synctify
- `playlists/current/` uses `rclone sync` so it represents the latest generated playlist set
- deleting a playlist may remove it from `current/`, while historical snapshots remain
- a failed stage stops later stages from running
- dry runs do not write snapshot history to SQLite

This keeps the audio backup non-destructive while preserving a clear current playlist view and historical playlist states.

## Local garbage collection

A local FLAC becomes collectible only after its track has zero references in the current playlist state, including the synthetic Liked Songs playlist. Removing a song from one playlist does not make it collectible while another playlist still references it.

Preview cleanup first:

```bash
synctify clean
```

`clean` is preview-only by default. To actually remove the safe candidates shown by the preview:

```bash
synctify clean --apply
```

Safety behavior:

- only tracks with zero current `playlist_tracks` references are considered
- files resolving outside Synctify's canonical `library/` are never deleted
- symlinks escaping the canonical library are never deleted
- a `local_path` shared by multiple track records is protected
- missing local files can have their stale local-path/hash state cleared during `--apply`
- source-service resolutions remain in SQLite after cleanup, so deleted tracks can be acquired again later
- empty artist/album directories are removed after their final file is deleted
- cleanup does not touch pCloud or other backup remotes; previously backed-up audio remains there

After cleanup, a later device mirror will propagate those local deletions to the device because mirror targets use `rclone sync`.

## Sync semantics

Synctify treats device synchronization and cloud backup as different operations:

- **Mirror** targets use source-of-truth semantics. Files removed locally are removed from the destination on the next sync. This is implemented for mounted filesystem targets.
- **Backup** targets keep remote-only audio files. Local FLAC deletion is not propagated to cloud backup storage.
- Playlist backup history is append-only, while the separate `current/` playlist view mirrors the local playlist set.

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
synctify update --source <source>
synctify resolve status
synctify resolve auto --source <source>
synctify resolve auto --source <source> --dry-run
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
