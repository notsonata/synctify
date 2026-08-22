# Synctify

**Current version: 1.2.0**

Synctify is a local-first macOS music library manager. Spotify supplies playlist metadata and the user's desired selections; Synctify resolves confirmed tracks against supported lossless source services, acquires one canonical local FLAC copy, builds M3U8 playlists, mirrors the library to devices, and keeps cloud backups non-destructive.

## Architecture

```text
Spotify playlist catalog
        ↓
User-reviewed playlists and tracks
        ↓
Confirmed Synctify desired state
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
   └── non-destructive rclone backup
```

Spotify is used for metadata and desired playlist state only. Synctify does not download audio from Spotify.

## Requirements

- macOS
- Python 3.12+
- Spotify developer application for playlist discovery
- Streamrip for automatic catalog resolution and for Tidal/Deezer acquisition
- qobuz-dl for the default Qobuz acquisition path; Qobuz may alternatively use Streamrip
- rclone for mirrors and cloud backups

Streamrip is required for automatic catalog resolution used by `synctify resolve auto` and `synctify update`. Installing qobuz-dl alone is sufficient only for acquiring tracks that already have Qobuz resolutions. External downloaders are not vendored by Synctify and must be installed/configured separately.

## Install

### macOS release ZIP

For normal use, download `synctify-1.2.0-macos.zip` from the GitHub Release and extract it. GitHub's automatically generated **Source code (zip)** and **Source code (tar.gz)** entries are repository snapshots; they are not the end-user launcher bundle.

From Terminal, enter the extracted folder and install the stable command:

```bash
./synctify.sh install
```

The installer stores the application under:

```text
~/Library/Application Support/Synctify/app/releases/1.2.0
```

and points `~/Library/Application Support/Synctify/app/current` at that version. It also creates `~/.local/bin/synctify` and adds `~/.local/bin` to your shell PATH when needed.

The first application launch finds Python 3.12+, creates a private `.venv` inside the active versioned application release, and installs the matching Python wheel bundled inside the macOS ZIP. Internet access is required during that first bootstrap for Python dependencies.

The GitHub Release publishes `synctify-1.2.0-macos.zip` and `synctify-1.2.0.tar.gz` as authored release assets. Streamrip, qobuz-dl, and rclone remain external tools.

After installation:

```bash
synctify setup
synctify doctor
synctify
```

With no arguments, `synctify` opens the Textual TUI.

### Application upgrades

Updating the Synctify application is separate from updating the music library:

```bash
synctify upgrade --check
synctify upgrade
```

Installed Synctify checks GitHub for a newer stable application release according to the configured auto-update policy. In the default interactive policy it asks:

```text
Synctify application 1.2.1 is available (current: 1.2.0). Upgrade now? [Y/n]
```

Update policy:

```bash
synctify config set auto-update prompt
synctify config set auto-update check
synctify config set auto-update install
synctify config set auto-update off
```

`SYNCTIFY_AUTO_UPDATE` overrides the saved policy. Private repositories can authenticate with `gh auth login` or `SYNCTIFY_GITHUB_TOKEN`; `GH_TOKEN` and `GITHUB_TOKEN` are also recognized.

## Spotify selection workflow

Synctify 1.2 separates Spotify discovery from the active music library. Fetching Spotify data never automatically downloads music.

### 1. Fetch the playlist catalog

```bash
synctify spotify fetch-playlists
```

This fetches the user's Spotify playlist list plus **Liked Songs**. Liked Songs behaves like a normal playlist. This command does **not** fetch every song in every playlist.

In the TUI, open the **Spotify** tab and choose **Fetch Playlists**. The playlist browser shows available and imported playlists with included, excluded, and pending counts.

### 2. Review one playlist at a time

Select a playlist and choose **Load / Review**. Synctify fetches only that playlist's tracks. For an unimported playlist, tracks start as pending choices. Mark tracks included or excluded, then choose **Confirm Playlist**.

After confirming one playlist, return to the playlist list and configure the next playlist. Synctify does not automatically walk every playlist or start a bulk download.

Track exclusions are persistent. If an excluded Spotify track remains in the playlist on a future refresh, it stays excluded.

### 3. Check imported playlists for Spotify changes

```bash
synctify spotify update-tracked
```

This fetches tracks only for playlists already imported into Synctify. New Spotify tracks appear as **pending additions**. Tracks removed from Spotify appear as **pending removals**. Neither change modifies the active desired library until reviewed in the Spotify tab and applied.

The Spotify sidebar shows:

- included tracks
- excluded tracks
- pending additions
- pending removals

Use **Apply Choices** after reviewing an imported playlist. Pending additions do not enter the resolution/download queue until explicitly included and applied.

### Unimporting a playlist

Choosing **Unimport** removes the generated Synctify playlist and removes that playlist's references from desired state. A local FLAC is deleted only when no other imported Synctify playlist still references that track. Shared tracks remain in the canonical library as long as any imported playlist needs them.

Cloud backups are different: **Synctify never deletes existing rclone backup objects when a playlist or local FLAC is unimported.** Backup operations use copy-only semantics for both the music library and playlist backup paths. Historical playlist snapshots remain untouched.

## Interactive TUI

Launch:

```bash
synctify
```

or explicitly:

```bash
synctify tui
```

The main tabs are:

```text
Dashboard | Spotify | Unresolved | Doctor | Audit | Commands
```

The Spotify tab is the primary import/update workflow. The Commands tab remains available for non-interactive operations such as `acquire --source tidal`, `sync phone`, `backup cloud`, and `config show`.

Long-running TUI actions show a visible loading indicator and status message. Conflicting action buttons are disabled while work is running. Press **Esc** or choose **Cancel (Esc)** to cancel the active cancellable operation. Child CLI processes are terminated and reaped on cancellation.

Keyboard shortcuts:

```text
1 Dashboard
2 Spotify
3 Unresolved
4 Doctor
5 Audit
6 Commands
i Spotify tab
u Library Update
r Refresh
Esc Cancel active operation
q Quit
```

## Library update

`synctify update` now means only the confirmed Synctify music-library workflow. It does not fetch Spotify playlists or silently add new Spotify tracks.

Preview:

```bash
synctify update --dry-run
```

Apply:

```bash
synctify update
```

The workflow:

1. uses the playlists/tracks already confirmed in Synctify
2. resolves unresolved confirmed tracks using Streamrip catalog search
3. acquires resolved tracks
4. reconciles safely matching existing FLAC files
5. rebuilds generated playlists

Useful controls:

```bash
synctify update --sources qobuz,tidal,deezer
synctify update --source qobuz
synctify update --search-results 10
synctify update --resolution-limit 25
synctify update --allow-partial
```

Long update and dry-run operations report their current stage to stderr. The final structured workflow report remains on stdout.

## Resolution and acquisition

Automatic fallback uses:

```text
Qobuz → Tidal → Deezer
```

Automatic Streamrip resolution currently requires a usable Spotify ISRC. Synctify searches both that ISRC and the track's artist/title and accepts a catalog candidate only when the same provider track ID appears in both query result sets. Tracks without a Spotify ISRC, or without cross-query agreement, remain unresolved for manual handling rather than being guessed from title/artist alone.

Inspect or override mappings:

```bash
synctify resolve status
synctify resolve auto --source qobuz --dry-run
synctify resolve auto --source qobuz
synctify resolve set SPOTIFY_TRACK_ID qobuz QOBUZ_TRACK_ID
synctify resolve clear SPOTIFY_TRACK_ID
```

Acquire resolved tracks:

```bash
synctify acquire --dry-run
synctify acquire --source qobuz
synctify acquire --source tidal
synctify acquire --source deezer
```

Existing FLAC reconciliation is constrained to the canonical library and only adopts a unique safe metadata/ISRC match.

## Source policy

The canonical library is lossless-only. Qobuz uses qobuz-dl by default for acquisition; Tidal and Deezer use Streamrip. Qobuz may also use Streamrip explicitly.

Supported qobuz-dl quality values are `6`, `7`, and `27`. Quality `5` is MP3 and is not accepted for new runtime settings.

Older Synctify config/portable files containing SoundCloud or Qobuz quality `5` remain readable for migration compatibility. At runtime SoundCloud is removed from source priority and legacy Qobuz quality `5` is normalized to lossless quality `6`.

## First-run setup

Interactive:

```bash
synctify setup
```

Scripted example:

```bash
synctify setup --non-interactive \
  --library /absolute/path/to/Music \
  --sources qobuz,tidal,deezer \
  --spotify-client-id YOUR_SPOTIFY_CLIENT_ID \
  --qobuz-dl /path/to/qobuz-dl \
  --streamrip /opt/homebrew/bin/rip \
  --rclone /opt/homebrew/bin/rclone \
  --qobuz-quality 27
```

Configure the Spotify application callback as:

```text
http://127.0.0.1:8765/callback
```

Then authenticate:

```bash
synctify spotify login --client-id YOUR_SPOTIFY_CLIENT_ID
synctify spotify fetch-playlists
```

Required Spotify scopes are `user-library-read`, `playlist-read-private`, and `playlist-read-collaborative`.

## Persistent configuration

Inspect effective values:

```bash
synctify config show
```

Common settings:

```bash
synctify config set library-dir /absolute/path/to/Music
synctify config set auto-update prompt
synctify config set source-priority qobuz,tidal,deezer
synctify config set qobuz-dl /path/to/qobuz-dl
synctify config set streamrip /opt/homebrew/bin/rip
synctify config set rclone /opt/homebrew/bin/rclone
```

Remove the library override:

```bash
synctify config unset library-dir
```

Precedence is:

```text
explicit CLI flag > environment variable > saved config > built-in default
```

Main environment overrides include `SYNCTIFY_LIBRARY_DIR`, `SYNCTIFY_AUTO_UPDATE`, `SYNCTIFY_SOURCES`, `SYNCTIFY_QOBUZ_DL`, `SYNCTIFY_STREAMRIP`, and `SYNCTIFY_RCLONE`.

## Playlists

```bash
synctify playlists build
synctify playlists build --allow-partial
```

Generated M3U8 files contain Synctify ownership markers. Stale owned outputs are removed safely when a local Synctify playlist is unimported or changes; unrelated user-managed M3U8 files are preserved.

## Audit and local cleanup

Read-only audit:

```bash
synctify audit
```

Apply safe database repairs:

```bash
synctify audit --repair
```

Preview or remove safe unreferenced local files:

```bash
synctify clean
synctify clean --apply
```

Local cleanup refuses unsafe paths and symlink targets. Spotify unimport uses the same safe unreferenced-track rules and only deletes a FLAC after its last imported-playlist reference is gone.

## Mirrors and backups

Filesystem mirrors intentionally use synchronization semantics:

```bash
synctify targets add phone /Volumes/Phone/Music
synctify sync phone --dry-run
synctify sync phone
```

Cloud backups are non-destructive:

```bash
synctify targets add-backup cloud pcloud:Synctify
synctify backup cloud --dry-run
synctify backup cloud
```

The rclone backup path uses copy-only semantics for the canonical library and current playlist files, plus versioned playlist snapshots. Removing content locally never instructs a backup target to delete its existing remote copy.

## Portable migration and relinking

Export/import setup metadata:

```bash
synctify export synctify-portable.json
synctify import synctify-portable.json
synctify import synctify-portable.json --apply
```

Run the coordinated migration workflow:

```bash
synctify migrate synctify-portable.json \
  --library /Volumes/MusicBackup/MyLibrary \
  --spotify-login \
  --apply
```

Resume or restart:

```bash
synctify migrate synctify-portable.json --resume --apply
synctify migrate synctify-portable.json --restart --apply
```

Relink an existing FLAC library:

```bash
synctify relink /Volumes/MusicBackup/MyLibrary
synctify relink /Volumes/MusicBackup/MyLibrary --apply
```

Relink copies/adopts only safe one-to-one matches for currently desired tracks. Source files are never moved or deleted.

## Database migration

Synctify 1.2 introduces schema v6 for the staged Spotify catalog, persistent track exclusions, and pending additions/removals. Normal initialization/setup applies the migration. Existing imported Spotify playlists are seeded into the new catalog as tracked with their existing tracks marked included.

Run this explicitly if Doctor reports an older schema:

```bash
synctify init
synctify doctor
```

## Development

```bash
python -m pip install -e '.[dev]'
python -m compileall -q src tests
python -m pytest
```

The installed console script points to the explicit root app `synctify.app:app`. Product changes must keep `pyproject.toml`, `synctify.__version__`, README release metadata, and `CHANGELOG.md` synchronized. GitHub Actions tests supported Python versions, builds the wheel and macOS launcher bundle, smoke-tests the installed command, and validates release tags before publishing.
