# Synctify

**Current version: 0.12.0**

Synctify is a local-first macOS music library manager. Spotify provides desired playlist/library state, Synctify maintains one canonical local lossless library, generates UTF-8 M3U8 playlists, mirrors that library to devices, and can maintain a non-destructive cloud backup.

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
   ├── mirror → phone / external drive
   └── backup → pCloud / rclone remote
```

Synctify uses Spotify for metadata and playlist state only. It does not download audio from Spotify. Source-service identity and downloader choice are separate concepts.

## Current state

Synctify currently includes:

- Python 3.12 package and `synctify` CLI
- SQLite state database with migrations
- Spotify Authorization Code with PKCE login
- OAuth refresh tokens stored in the system keychain
- Liked Songs and accessible playlist ingestion
- Spotify IDs, ISRCs, metadata, ordering, and change planning
- coordinated `update` workflow
- transactional `update --dry-run`
- ordered automatic source fallback
- deterministic source-service matching with ambiguity protection
- persistent manual overrides
- Qobuz acquisition through external qobuz-dl
- Qobuz, Tidal, Deezer, and SoundCloud search/download support through external Streamrip
- downloaded FLAC path and SHA-256 persistence
- UTF-8 M3U8 playlist generation
- mounted-filesystem mirrors using rclone
- non-destructive rclone cloud backups with playlist snapshots
- safe local garbage collection
- macOS GitHub Actions tests

## Spotify setup

Create a Spotify developer application and add this Redirect URI:

```text
http://127.0.0.1:8765/callback
```

Then run:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
```

Required scopes:

```text
user-library-read
playlist-read-private
playlist-read-collaborative
```

Spotify currently exposes playlist items to Synctify only when the authenticated user owns the playlist or is a collaborator. Followed non-collaborative playlists owned by somebody else are therefore not imported.

## Coordinated update workflow

The normal update path performs:

```text
Spotify pull
   ↓
ordered automatic source resolution
   ↓
acquire every currently resolved desired track
   ↓
rebuild M3U8 playlists
```

Run it:

```bash
synctify update
```

The default automatic-resolution priority is:

```text
qobuz → tidal → deezer → soundcloud
```

Fallback is per track. If a track resolves safely on Qobuz, Synctify does not search later sources for that track. If Qobuz returns no safe match, an ambiguous match, a low-confidence match, or a search error, the unresolved track can continue to Tidal and then later sources.

A search error that is recovered by a later source is reported but does not make the whole update fail. An unrecovered operational search error still causes a non-zero workflow result.

Customize the fallback order:

```bash
synctify update --sources qobuz,tidal
synctify update --sources tidal,qobuz,deezer
```

Use exactly one automatic source with the backward-compatible shortcut:

```bash
synctify update --source qobuz
synctify update --source tidal
```

`--source` overrides `--sources`.

Preview the complete post-pull state without persisting anything:

```bash
synctify update --dry-run
```

The dry run fetches Spotify, applies the new desired state inside a SQLite savepoint, simulates the full fallback chain, calculates the resulting download plan, checks playlist readiness, then rolls the savepoint back. It performs no downloads and writes no M3U8 files.

Useful controls:

```bash
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
synctify update --qobuz-dl /path/to/qobuz-dl/.venv/bin/qobuz-dl
synctify update --streamrip /opt/homebrew/bin/rip
```

`--resolution-limit` limits the number of distinct unresolved desired tracks selected for the entire fallback run. It is not reset for every source.

The automatic source priority affects only unresolved tracks. Acquisition processes every pending desired track with an existing stored source resolution, regardless of which catalog was searched during the current update.

Successful stages are committed as the workflow progresses. A failed download does not undo the successful Spotify pull or safe source resolutions, and playlist building still runs from whatever local files are safely available.

## Lower-level Spotify and resolution commands

Refresh Spotify state only:

```bash
synctify spotify pull
```

Inspect resolution state:

```bash
synctify resolve status
```

Run single-source automatic resolution directly:

```bash
synctify resolve auto --source qobuz --dry-run
synctify resolve auto --source qobuz
synctify resolve auto --source tidal
```

Synctify resolves candidates conservatively:

1. stored manual override
2. exact normalized ISRC when candidate metadata exposes one
3. normalized metadata score
4. unresolved or ambiguous when confidence is insufficient

Automatic metadata matches require at least `0.88`. If the top two candidates are within `0.04`, Synctify refuses to auto-select either one.

Persist or clear a manual source mapping:

```bash
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve set SPOTIFY_TRACK_ID tidal TIDAL_TRACK_ID
synctify resolve clear SPOTIFY_TRACK_ID
```

Existing automatic and manual mappings are not overwritten by automatic resolution. Clear a mapping first if you intentionally want it reconsidered.

## External downloaders

Synctify does not vendor or copy downloader internals. External tools remain separately installed applications.

### qobuz-dl

Synctify uses [Sei969/qobuz-dl](https://github.com/Sei969/qobuz-dl) as the default downloader for Qobuz.

A separate clone is required:

```bash
git clone https://github.com/Sei969/qobuz-dl.git
```

Complete qobuz-dl's upstream setup and Qobuz authentication. Put `qobuz-dl` on `PATH`, pass `--qobuz-dl`, or set `SYNCTIFY_QOBUZ_DL`.

Check it:

```bash
synctify qobuz doctor
```

### Streamrip

[nathom/streamrip](https://github.com/nathom/streamrip) is Synctify's multi-service catalog-search adapter and the default downloader for non-Qobuz source resolutions.

On macOS:

```bash
brew install streamrip
```

Put `rip` on `PATH`, pass `--streamrip`, or set `SYNCTIFY_STREAMRIP`.

Check it:

```bash
synctify streamrip doctor
```

## Acquisition

Qobuz defaults to qobuz-dl:

```bash
synctify acquire --source qobuz --dry-run
synctify acquire --source qobuz
```

Other Streamrip-supported sources:

```bash
synctify acquire --source tidal
synctify acquire --source deezer
synctify acquire --source soundcloud
```

Qobuz can explicitly use Streamrip if needed:

```bash
synctify acquire --source qobuz --downloader streamrip
```

Streamrip acquisitions use exact source IDs rather than guessed URLs. Synctify records resulting paths, hashes, local status, and source resolutions in its own SQLite state.

A direct qobuz-dl diagnostic passthrough is also available:

```bash
synctify qobuz download-url https://open.qobuz.com/track/123456789 -d /tmp/qobuz-test
```

## Playlist generation

```bash
synctify playlists build
```

The default is strict: if any track in a playlist is unavailable locally, Synctify reports it and does not write that playlist.

Opt into partial playlists:

```bash
synctify playlists build --allow-partial
```

M3U8 entries use relative UTF-8 paths into the canonical library.

## Device mirrors

Filesystem targets are intended for mounted phones, SD cards, USB drives, and external SSDs.

```bash
synctify targets add phone /Volumes/Phone/Music
synctify sync phone --dry-run
synctify sync phone
```

The destination layout is:

```text
/Volumes/Phone/Music/
├── library/
└── playlists/
```

Device mirrors use `rclone sync`, so local deletion propagates to the target. Synctify refuses missing/unmounted destination roots and destinations overlapping its own application-data directory.

## pCloud and rclone backups

Configure a pCloud remote with `rclone config`, then add it:

```bash
synctify targets add-backup cloud pcloud:Synctify
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

Backup semantics intentionally differ from device mirrors:

- `library/` uses `rclone copy`, so deleting a local FLAC does not delete its remote backup
- `playlists/current/` mirrors the latest generated playlist set
- `playlists/snapshots/` is append-only history
- unchanged playlists do not create duplicate snapshots
- dry runs do not modify remote or snapshot state

## Local garbage collection

Preview only:

```bash
synctify clean
```

Apply deletion:

```bash
synctify clean --apply
```

A local FLAC is collectible only when its track has zero current playlist references. Paths outside the canonical library, symlink escapes, and shared local paths are protected. Source resolutions and remote backup history remain available after local cleanup.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Set `SYNCTIFY_HOME` to override the default application-data directory during development or testing.

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
synctify update --source <single-source>
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
