# Synctify

**Current version: 1.0.6**

Synctify is a local-first macOS music library manager. Spotify defines the desired playlist/library state; Synctify resolves those tracks against supported lossless source services, acquires one canonical local FLAC copy, builds M3U8 playlists, mirrors the library to devices, and can keep a non-destructive cloud backup.

## Architecture

```text
Spotify desired state
        ↓
Automatic resolution via Streamrip catalog search
Qobuz → Tidal → Deezer
        ↓
External downloader
qobuz-dl or Streamrip
        ↓
Canonical FLAC library
        ↓
M3U8 playlists
   ├── filesystem mirror
   └── rclone backup
```

Spotify is used for metadata and desired playlist state only. Synctify does not download audio from Spotify.

## Requirements

- macOS
- Python 3.12+
- Spotify developer application for playlist/library import
- Streamrip for automatic catalog resolution and for Tidal/Deezer acquisition
- qobuz-dl for the default Qobuz acquisition path; Qobuz may alternatively use Streamrip
- rclone for mirrors and cloud backups

Streamrip is required for automatic catalog resolution used by `synctify resolve auto` and the coordinated `synctify update` workflow. Installing qobuz-dl alone is sufficient only for acquiring tracks that already have Qobuz resolutions. External downloaders are not vendored by Synctify and must be installed/configured separately.

## Install

### macOS release ZIP

For normal use, download `synctify-1.0.6-macos.zip` from the GitHub Release and extract it. GitHub's automatically generated **Source code (zip)** and **Source code (tar.gz)** entries are repository snapshots; they are not the end-user launcher bundle.

From Terminal, enter the extracted folder and install the stable command:

```bash
./synctify.sh install
```

The installer stores the application under:

```text
~/Library/Application Support/Synctify/app/releases/1.0.6
```

and points:

```text
~/Library/Application Support/Synctify/app/current
```

at that version. It also creates:

```text
~/.local/bin/synctify
```

and adds `~/.local/bin` to your shell PATH when needed. If the installer changes your shell profile, restart Terminal or source the profile path it prints.

After installation, run Synctify from anywhere:

```bash
synctify setup
synctify doctor
synctify update --dry-run
synctify update
synctify
```

With no arguments, `synctify` opens the Textual TUI.

The extracted bundle remains usable in portable mode without installing the stable command:

```bash
./synctify.sh
./synctify.sh setup
./synctify.sh doctor
```

The first application launch finds Python 3.12+, creates a private `.venv` inside the active versioned application release, and installs the bundled Synctify wheel. Internet access is required during that first bootstrap so pip can install the wheel's Python dependencies. Set `SYNCTIFY_PYTHON` if you want the launcher to use a specific compatible interpreter.

The stable `~/.local/bin/synctify` wrapper always launches `app/current/synctify.sh`. New releases are installed into a new versioned directory before `current` is switched, so the command path remains stable across updates.

Synctify's database, configuration, canonical library, and playlists remain separate from the replaceable application release. The database, configuration, and generated playlists use the normal macOS application-data location. The canonical library uses that location by default but can be pointed at another absolute directory during setup or with `synctify config set library-dir /absolute/path/to/Music`. Set `SYNCTIFY_HOME` to override the application-data directory.

The GitHub Release publishes `synctify-1.0.6-macos.zip` and `synctify-1.0.6.tar.gz` as the authored release assets. The matching Python wheel is bundled inside the macOS ZIP rather than published as a separate asset. Streamrip, qobuz-dl, and rclone remain external tools and are not included in the ZIP.

### Self-update

Installed Synctify checks GitHub for a newer stable release every time the `synctify` command is run. The default policy is `prompt`. If a newer release exists in an interactive terminal, Synctify asks before installing it:

```text
Synctify 1.0.7 is available (current: 1.0.6). Update now? [Y/n]
```

Answering yes downloads the matching macOS release bundle, validates its stable tag, expected asset name, archive layout, embedded `VERSION`, and GitHub-provided SHA-256 digest when available, installs it into `app/releases/<version>`, switches `app/current`, and restarts the original command under the new version.

Non-interactive scripts are never blocked waiting for a prompt. They print an update notice and continue with the current version. Portable release bundles and development checkouts do not perform automatic checks.

Explicit update commands:

```bash
synctify self-update --check
synctify self-update
```

Update policy:

```bash
synctify config set auto-update prompt   # default: ask before installing
synctify config set auto-update check    # notify only
synctify config set auto-update install  # install without asking
synctify config set auto-update off      # disable automatic checks
```

`SYNCTIFY_AUTO_UPDATE` overrides the saved update policy.

The repository may require authentication to read GitHub Releases. For a private repository, authenticate GitHub CLI with `gh auth login`, or provide `SYNCTIFY_GITHUB_TOKEN`. `GH_TOKEN` and `GITHUB_TOKEN` are also recognized.

Update-check failures are non-fatal. If GitHub is unavailable, the requested Synctify command continues normally.

### Development checkout

From a cloned source checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
synctify setup
```

## Interactive TUI

Launch the terminal user interface with:

```bash
synctify
```

The explicit CLI form remains available:

```bash
synctify tui
```

When using an extracted release ZIP without installing it, use:

```bash
./synctify.sh
```

The 1.0 TUI is an interactive frontend over Synctify's existing local state and service functions. It includes:

- a dashboard for track, local-FLAC, resolution, pending-download, playlist, target, and Spotify-pull state
- an unresolved-track table with manual Qobuz/Tidal/Deezer mapping
- Doctor results with pass/warn/fail checks
- read-only library Audit results
- keyboard tab navigation (`1`–`4`), `r` to refresh, and `q` to quit

Launching the TUI does not initialize or migrate an absent/older database. Doctor and Audit are run in background workers so their filesystem/tool checks do not block the interface.

The normal CLI remains fully supported and is still the automation interface. In 1.0, coordinated update/acquisition, sync/backup, migration, setup, and repair operations remain CLI commands rather than being duplicated inside the TUI.

## Source policy

The canonical library is lossless-only. Automatic fallback uses:

```text
Qobuz → Tidal → Deezer
```

Automatic resolution uses Streamrip catalog search for all supported sources. Qobuz uses qobuz-dl by default for acquisition; Tidal and Deezer use Streamrip. Qobuz may also use Streamrip explicitly.

Supported qobuz-dl quality values are `6`, `7`, and `27`. Quality `5` is MP3 and is not accepted for new runtime settings.

Older Synctify config/portable files that contain SoundCloud or Qobuz quality `5` remain readable for migration compatibility. At runtime, SoundCloud is removed from source priority and legacy Qobuz quality `5` is normalized to lossless quality `6`; Synctify does not use SoundCloud for automatic resolution or acquisition into the canonical FLAC library.

## First-run setup

Interactive setup:

```bash
synctify setup
```

Interactive setup prompts for the canonical music library directory. Press Enter to keep the default, or enter an absolute path to your existing library.

Scripted setup:

```bash
synctify setup --non-interactive \
  --library /absolute/path/to/Music \
  --sources qobuz,tidal,deezer \
  --spotify-client-id YOUR_SPOTIFY_CLIENT_ID \
  --qobuz-dl /path/to/qobuz-dl \
  --streamrip /opt/homebrew/bin/rip \
  --rclone /opt/homebrew/bin/rclone \
  --qobuz-quality 27 \
  --streamrip-qobuz-quality 4 \
  --streamrip-tidal-quality 3 \
  --streamrip-deezer-quality 2
```

Optional target provisioning:

```bash
synctify setup --non-interactive \
  --mirror-name phone \
  --mirror-destination /Volumes/Phone/Music \
  --backup-name cloud \
  --backup-destination pcloud:Synctify
```

Run a read-only setup check afterward:

```bash
synctify doctor
```

## Spotify

Configure your Spotify application with the loopback callback:

```text
http://127.0.0.1:8765/callback
```

Then authenticate and import desired state:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
synctify spotify pull
```

Required scopes:

```text
user-library-read
playlist-read-private
playlist-read-collaborative
```

A temporarily inaccessible Spotify playlist is preserved in local desired state instead of being deleted.

## Coordinated update

Preview without persistent DB/filesystem changes:

```bash
synctify update --dry-run
```

Apply the full workflow:

```bash
synctify update
```

Long update and dry-run operations report their current stage to stderr, including Spotify fetching, automatic-resolution source and track counts, download planning/acquisition, playlist readiness, and playlist rebuilding. The final structured workflow report remains on stdout.

The workflow:

1. refreshes Spotify desired state
2. resolves unresolved desired tracks using Streamrip catalog search and the configured source priority
3. acquires resolved tracks
4. reconciles safely matching existing FLAC files when a downloader reports an existing download
5. rebuilds generated playlists

Useful controls:

```bash
synctify update --sources qobuz,tidal,deezer
synctify update --source qobuz
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
```

`--resolution-limit` freezes one bounded set of unresolved desired tracks across the whole fallback chain.

## Resolution and acquisition

Inspect or override source mappings:

```bash
synctify resolve status
synctify resolve auto --source qobuz --dry-run
synctify resolve auto --source qobuz
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve clear SPOTIFY_TRACK_ID
```

Automatic Streamrip resolution currently requires a usable Spotify ISRC. Synctify searches both that ISRC and the track's artist/title, and accepts a catalog candidate only when the same provider track ID appears in both query result sets. Tracks without a Spotify ISRC, or without cross-query agreement, remain unresolved for manual handling rather than being guessed from title/artist alone.

The generic deterministic matcher used for richer candidates and local-FLAC reconciliation still supports normalized ISRC plus title/artist/album/duration scoring, ambiguity protection, and gross-duration mismatch rejection when that metadata is available.

Inspect/acquire resolved tracks:

```bash
synctify acquire --dry-run
synctify acquire --source qobuz
synctify acquire --source tidal
synctify acquire --source deezer
```

Existing FLAC reconciliation is constrained to the canonical library and only adopts a unique safe metadata/ISRC match.

## Persistent configuration

Inspect effective values after CLI/environment/config precedence:

```bash
synctify config show
```

Set routine defaults:

```bash
synctify config set library-dir /absolute/path/to/Music
synctify config set auto-update prompt
synctify config set source-priority qobuz,tidal,deezer
synctify config set qobuz-dl /path/to/qobuz-dl
synctify config set streamrip /opt/homebrew/bin/rip
synctify config set rclone /opt/homebrew/bin/rclone
synctify config set qobuz-quality 27
synctify config set streamrip-qobuz-quality 4
synctify config set streamrip-tidal-quality 3
synctify config set streamrip-deezer-quality 2
```

Remove the saved library override to return to the built-in home-relative library:

```bash
synctify config unset library-dir
```

Precedence:

```text
explicit CLI flag > environment variable > saved config > built-in default
```

Main environment overrides:

```text
SYNCTIFY_LIBRARY_DIR
SYNCTIFY_AUTO_UPDATE
SYNCTIFY_SOURCES
SYNCTIFY_QOBUZ_DL
SYNCTIFY_STREAMRIP
SYNCTIFY_RCLONE
SYNCTIFY_QOBUZ_QUALITY
SYNCTIFY_STREAMRIP_QOBUZ_QUALITY
SYNCTIFY_STREAMRIP_TIDAL_QUALITY
SYNCTIFY_STREAMRIP_DEEZER_QUALITY
```

## Playlists

```bash
synctify playlists build
synctify playlists build --allow-partial
```

Strict mode does not write incomplete playlists. Partial mode writes the tracks that are currently available.

Generated M3U8 files contain Synctify ownership markers. Stale owned outputs are removed safely when playlists disappear, become incomplete, or are renamed; unrelated user-managed M3U8 files are preserved.

## Audit and garbage collection

Read-only library audit:

```bash
synctify audit
```

Apply safe database repairs:

```bash
synctify audit --repair
```

Preview/remove safe unreferenced local files:

```bash
synctify clean
synctify clean --apply
```

Audit can repair missing hashes, stale local-path state, and uniquely match untracked FLACs. Garbage collection refuses unsafe paths and does not follow a recorded symlink to delete its target.

## Mirrors and backups

Filesystem mirror:

```bash
synctify targets add phone /Volumes/Phone/Music
synctify sync phone --dry-run
synctify sync phone
```

Cloud backup:

```bash
synctify targets add-backup cloud pcloud:Synctify
synctify backup cloud --dry-run
synctify backup cloud
```

Mirrors use `rclone sync` for managed roots. Backups use non-destructive library copy semantics and keep versioned playlist snapshots.

## Portable migration

Export portable setup metadata:

```bash
synctify export synctify-portable.json
```

Preview/apply it on another Mac:

```bash
synctify import synctify-portable.json
synctify import synctify-portable.json --apply
```

Portable state excludes Spotify keychain tokens, local FLAC paths/hashes, source-resolution rows, and sync history.

To restore setup, desired state, optional copied FLACs, playlists, audit, and diagnostics as one checkpointed workflow:

```bash
synctify migrate synctify-portable.json \
  --library /Volumes/MusicBackup/MyLibrary \
  --spotify-login \
  --apply
```

Resume an interrupted compatible migration:

```bash
synctify migrate synctify-portable.json \
  --library /Volumes/MusicBackup/MyLibrary \
  --resume \
  --apply
```

Or discard the checkpoint and intentionally rerun every stage:

```bash
synctify migrate synctify-portable.json --restart --apply
```

Completed relink stages can be resumed even if the old removable source is no longer mounted.

## Relinking an existing FLAC library

Preview:

```bash
synctify relink /Volumes/MusicBackup/MyLibrary
```

Apply:

```bash
synctify relink /Volumes/MusicBackup/MyLibrary --apply
```

Relink copies/adopts only safe one-to-one matches for currently desired tracks. Source files are never moved or deleted, copied files are SHA-256 verified, and one source FLAC cannot be assigned automatically to multiple desired tracks across limited runs.

## CLI composition

The installed console script points to one explicit root app: `synctify.app:app`. Top-level and nested commands are registered once in that composition module, with regression tests locking the command surface. Legacy command modules remain import-compatible internally but no longer determine the installed CLI through import-order command replacement.

## Development

```bash
python -m pip install -e '.[dev]'
python -m compileall -q src tests
python -m pytest
```

Every feature or fix should bump the project version and keep `pyproject.toml`, `synctify.__version__`, README release metadata, and `CHANGELOG.md` synchronized. GitHub Actions runs the test suite on macOS across supported Python versions, smoke-tests the built wheel, builds the macOS launcher ZIP, verifies the stable installer command, and executes the launcher from the extracted bundle before a release can be tagged.
