# Synctify

**Current version: 0.21.0**

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
- first-run interactive and scripted setup through `synctify setup`
- portable setup metadata export/import for migration between Macs
- migration relink/copy of an existing FLAC library without redownloading matched tracks
- coordinated migration bootstrap through `synctify migrate`
- SQLite state database with migrations
- top-level read-only setup diagnostics through `synctify doctor`
- persistent defaults for source priority, downloader quality, and executable paths
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
- generated-playlist ownership and stale-output cleanup
- mounted-filesystem mirrors through rclone
- non-destructive rclone cloud backups with playlist snapshots
- safe local garbage collection
- macOS GitHub Actions tests

## Installation

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
synctify setup
```

Set `SYNCTIFY_HOME` to override the default application-data directory.

## First-run setup

Run the interactive wizard:

```bash
synctify setup
```

The wizard can:

- initialize the Synctify home, canonical library, playlist directory, and SQLite schema
- save source priority
- save qobuz-dl, Streamrip, and rclone executable paths
- save qobuz-dl and source-specific Streamrip quality defaults
- save a Spotify Client ID and loopback redirect URI
- optionally open Spotify browser OAuth and store the token in the system keychain
- optionally create one filesystem mirror target
- optionally create one rclone backup target
- report whether the configured external executables are currently discoverable

Setup does not install qobuz-dl, Streamrip, or rclone. Missing tools are reported as warnings so setup can still finish. Except when `--spotify-login` is explicitly requested, setup does not contact Spotify or any music source service.

The setup command is safe to rerun. Existing mirror/backup targets with exactly the same settings are kept. A conflicting target with the same name is never silently replaced.

For scripted provisioning, use `--non-interactive`:

```bash
synctify setup --non-interactive \
  --sources qobuz,tidal,deezer,soundcloud \
  --spotify-client-id YOUR_SPOTIFY_CLIENT_ID \
  --qobuz-dl /path/to/qobuz-dl \
  --streamrip /opt/homebrew/bin/rip \
  --rclone /opt/homebrew/bin/rclone \
  --qobuz-quality 27 \
  --streamrip-qobuz-quality 4 \
  --streamrip-tidal-quality 3 \
  --streamrip-deezer-quality 2 \
  --streamrip-soundcloud-quality 2
```

Optional target provisioning:

```bash
synctify setup --non-interactive \
  --mirror-name phone \
  --mirror-destination /Volumes/Phone/Music \
  --backup-name cloud \
  --backup-destination pcloud:Synctify
```

Add `--spotify-login` when you explicitly want setup to launch browser OAuth after saving the Spotify configuration.

After setup:

```bash
synctify doctor
```

## Portable setup migration

Export reproducible setup metadata to a JSON file:

```bash
synctify export synctify-portable.json
```

The portable format is versioned and contains only:

- explicit values saved in `config.json`
- the public Spotify Client ID and redirect URI from `spotify.json`, when configured
- filesystem mirror and rclone backup target definitions

It intentionally does **not** contain Spotify access/refresh tokens, keychain data, local FLAC paths or hashes, playlist/track rows, source-resolution state, acquisition state, sync history, or cloud snapshot history.

Export refuses to overwrite an existing file unless explicitly requested:

```bash
synctify export synctify-portable.json --force
```

On another Mac, preview the import first:

```bash
synctify import synctify-portable.json
```

Preview mode validates the whole file and target compatibility without creating the Synctify home or changing local state. Apply it explicitly with:

```bash
synctify import synctify-portable.json --apply
```

Import merges only the saved config keys present in the bundle, so unrelated machine-local overrides are preserved. Public Spotify configuration is created or replaced as shown in the preview, but no Spotify token is imported. Existing identical targets are reused. A target with the same name but different settings aborts the import before config changes are written.

## Library migration and relink

After importing setup metadata on another Mac, authenticate Spotify and recreate desired playlist/library state before relinking audio:

```bash
synctify spotify login
synctify spotify pull
```

Preview an existing copied FLAC tree:

```bash
synctify relink /Volumes/MusicBackup/MyLibrary
```

Relink scans tagged FLAC files and uses the same exact-ISRC and deterministic metadata matcher used by acquisition and audit. It only targets tracks that are currently referenced by desired Spotify state and that do not already have a usable file inside the canonical Synctify library.

Apply safe matches explicitly:

```bash
synctify relink /Volumes/MusicBackup/MyLibrary --apply
```

Migration behavior:

- if the matched FLAC is already inside the canonical library, Synctify adopts it in place and only repairs SQLite local path/hash state
- if the matched FLAC is outside the canonical library, Synctify copies it into the canonical library while preserving the source-relative folder structure
- source FLACs are never moved or deleted
- copied files are SHA-256 verified before SQLite is updated
- an existing identical destination is reused
- a conflicting destination is never overwritten; Synctify uses a deterministic `[synctify-<id>]` filename when safe, otherwise reports a failure
- duplicate/ambiguous matches remain unresolved
- one source FLAC is not automatically assigned to multiple desired tracks
- already-local desired tracks are skipped
- `--limit N` can restrict the number of missing desired tracks inspected in one run

This command does not download audio or change source-service resolutions.

## Coordinated migration bootstrap

Use `migrate` when moving Synctify to another Mac and you want the restore stages coordinated for you.

Preview the migration first:

```bash
synctify migrate synctify-portable.json \
  --library /Volumes/MusicBackup/MyLibrary
```

Preview validates the portable bundle, target conflicts, optional library source, and planned stages. It does not create the Synctify home, contact Spotify, copy FLACs, or change SQLite.

Apply the migration explicitly:

```bash
synctify migrate synctify-portable.json \
  --library /Volumes/MusicBackup/MyLibrary \
  --spotify-login \
  --apply
```

The apply workflow runs in this order:

1. apply the validated portable setup metadata
2. optionally launch Spotify browser OAuth when `--spotify-login` is supplied
3. fetch and apply current Spotify desired state
4. optionally relink/copy safe FLAC matches from `--library`
5. rebuild generated playlists
6. run a read-only library audit
7. run `doctor`-equivalent final diagnostics

If a valid Spotify token is already present, omit `--spotify-login`. A Spotify login/pull failure stops the later desired-state-dependent stages. Once desired state has been restored, per-track relink failures are retained in the final report and the playlist/audit/doctor stages still run so the remaining migration gaps are visible.

Useful controls:

```bash
synctify migrate synctify-portable.json --apply
synctify migrate synctify-portable.json --library /path/to/flacs --apply
synctify migrate synctify-portable.json --library /path/to/flacs --relink-limit 100 --apply
synctify migrate synctify-portable.json --library /path/to/flacs --allow-partial --apply
```

`migrate` does not invoke qobuz-dl or Streamrip. Tracks that are still missing after relink remain available for the normal `resolve` / `acquire` / `update` workflow.

## Persistent configuration

Synctify can store routine defaults in:

```text
<SYNCTIFY_HOME>/config.json
```

Only explicit overrides are written. Missing keys continue to use environment variables or built-in defaults.

Inspect effective values:

```bash
synctify config show
```

Set defaults:

```bash
synctify config set source-priority qobuz,tidal,deezer,soundcloud
synctify config set qobuz-dl /path/to/qobuz-dl
synctify config set streamrip /opt/homebrew/bin/rip
synctify config set rclone /opt/homebrew/bin/rclone
synctify config set qobuz-quality 27
synctify config set streamrip-qobuz-quality 4
synctify config set streamrip-tidal-quality 3
synctify config set streamrip-deezer-quality 2
synctify config set streamrip-soundcloud-quality 2
```

Remove one saved override:

```bash
synctify config unset source-priority
```

Precedence is:

```text
explicit CLI flag > environment variable > saved config > built-in default
```

Executable environment variables remain supported:

```text
SYNCTIFY_QOBUZ_DL
SYNCTIFY_STREAMRIP
SYNCTIFY_RCLONE
```

Source/quality environment overrides are also supported:

```text
SYNCTIFY_SOURCES
SYNCTIFY_QOBUZ_QUALITY
SYNCTIFY_STREAMRIP_QOBUZ_QUALITY
SYNCTIFY_STREAMRIP_TIDAL_QUALITY
SYNCTIFY_STREAMRIP_DEEZER_QUALITY
SYNCTIFY_STREAMRIP_SOUNDCLOUD_QUALITY
```

Saved executable defaults are used by `doctor`, `resolve auto`, `update`, `acquire`, `sync`, `backup`, and the downloader-specific doctor commands. Saved downloader quality defaults are used by routine acquisition and coordinated updates when no more specific override applies.

## Diagnostics

Run a read-only health check of the local setup:

```bash
synctify doctor
```

The doctor checks:

- Synctify home, library, and playlist directories
- SQLite integrity, schema version, and required core tables
- Spotify configuration and whether a refresh-capable token exists in the system keychain
- qobuz-dl, Streamrip, and rclone executable availability
- locally configured rclone remote names through `rclone listremotes`
- mounted-filesystem mirror destinations
- configured rclone backup destinations

It does not create directories, initialize or migrate SQLite, refresh Spotify tokens, contact Spotify/Qobuz/Tidal/Deezer/SoundCloud, download audio, or alter target state.

Checks are classified as `PASS`, `WARN`, or `FAIL`. Missing optional tools, an uninitialized setup, an unplugged mirror target, and an unconfigured backup remote are warnings. Database corruption, invalid schema state, unsafe mirror overlap, and malformed configured targets are failures. `synctify doctor` exits with code 2 only when failures are present.

Executable paths can be inspected explicitly:

```bash
synctify doctor --qobuz-dl /path/to/qobuz-dl
synctify doctor --streamrip /opt/homebrew/bin/rip
synctify doctor --rclone /opt/homebrew/bin/rclone
```

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

Complete qobuz-dl's upstream authentication/setup separately. Put the executable on `PATH`, pass `--qobuz-dl`, set `SYNCTIFY_QOBUZ_DL`, or save it with `synctify config set qobuz-dl ...`.

### Streamrip

[nathom/streamrip](https://github.com/nathom/streamrip) is used for catalog search and for Tidal, Deezer, SoundCloud, and optional Qobuz acquisition.

```bash
brew install streamrip
synctify streamrip doctor
```

Put `rip` on `PATH`, pass `--streamrip`, set `SYNCTIFY_STREAMRIP`, or save it with `synctify config set streamrip ...`.

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

Generated M3U8 files have explicit ownership state in SQLite schema v5 and include a marker immediately after `#EXTM3U`:

```text
#EXTM3U
#SYNCTIFY:playlist-id=<spotify-playlist-id>
```

Ownership lets Synctify safely maintain the generated playlist directory:

- if a previously complete playlist becomes incomplete under strict mode, its previously generated owned M3U8 is removed
- if a Spotify playlist is removed, its owned generated M3U8 is removed on the next playlist build/update
- if a playlist is renamed or its duplicate-name disambiguation changes, the previous owned filename is removed after the replacement is written
- unknown `.m3u8` files are never deleted merely because their names resemble a Spotify playlist
- if the normal generated filename is occupied by an unmarked user file, Synctify preserves it and chooses a deterministic `[synctify-...]` filename instead
- if a file recorded as owned no longer carries the matching ownership marker, Synctify treats it as protected and relinquishes ownership rather than deleting it
- existing pre-v0.15 Synctify outputs are adopted only when their full legacy content exactly matches the playlist Synctify is about to render

This prevents stale generated playlists from propagating to device mirrors or the cloud `playlists/current/` view while avoiding deletion of unrelated user-maintained playlists.

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
