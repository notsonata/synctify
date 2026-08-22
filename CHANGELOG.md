# Changelog

## 1.2.0 - 2026-08-23

Synctify 1.2.0 replaces the all-at-once Spotify import with a staged, user-reviewed playlist workflow and makes long-running TUI actions visibly cancellable.

### Staged Spotify playlist selection

- add `synctify spotify fetch-playlists` to fetch the Spotify playlist catalog plus Liked Songs without fetching every playlist track
- treat Liked Songs exactly like a normal selectable playlist
- add `synctify spotify update-tracked` to refresh only playlists already imported into Synctify
- remove the public all-at-once `synctify spotify pull` command from the installed CLI
- store Spotify catalog state separately from active Synctify desired state so discovery cannot silently trigger resolution or downloads
- let users load and configure one playlist at a time, then return to the list to configure the next playlist
- persist per-track exclusions across Spotify refreshes
- surface new Spotify tracks as pending additions rather than automatically importing them
- surface tracks removed from Spotify as pending removals so destructive changes remain reviewable
- prevent the initial Confirm Playlist action from accepting pending additions on a playlist that is already imported
- include followed/non-owned playlists returned by the user's Spotify playlist endpoint instead of restricting discovery to owned/collaborative playlists

### Spotify TUI browser

- add a dedicated Spotify tab with playlist and per-track tables
- show included, excluded, pending-addition, and pending-removal counts in the playlist detail sidebar
- add Fetch Playlists, Update Tracked, Load / Review, Confirm Playlist, Apply Choices, Include Track, Exclude Track, and Unimport actions
- add a visible loading indicator and active-operation status for long-running TUI actions
- disable conflicting buttons while work is running
- bind Escape and a Cancel button to cancellation of Spotify work and child CLI processes
- terminate and reap child CLI processes when a TUI command is cancelled

### Desired-state and cleanup safety

- make `synctify update` operate only on playlists and tracks already confirmed in Synctify; Spotify fetching is now explicit and separate
- when a playlist is unimported, remove its generated local playlist and delete local FLACs only after their final imported-playlist reference is gone
- preserve a shared FLAC while any other imported playlist still references the track
- keep Spotify-side removals pending until explicitly reviewed and applied

### Non-destructive cloud backups

- preserve existing remote music files when local tracks are unimported
- change the `playlists/current` rclone operation from `sync` to copy-only semantics
- ensure Synctify backup operations never issue remote-delete synchronization for cloud backup targets
- keep historical playlist snapshots intact

### Database and release integration

- add schema v6 tables for the Spotify playlist catalog and persistent per-playlist item state
- migrate existing imported Spotify playlists into the new catalog as tracked with their existing tracks included
- update Doctor to validate the schema v6 selection tables
- update CLI composition, TUI, backup, Spotify-selection, package, and release regression coverage

SQLite schema migration **is required** for this release. Running normal setup/initialization, or `synctify init`, applies schema v6.

## 1.1.1 - 2026-08-23

Synctify 1.1.1 separates application upgrades from music-library updates so the two operations no longer share confusing terminology.

### Upgrade command

- replace the public `synctify self-update` command with `synctify upgrade`
- keep `synctify upgrade --check` for checking the latest stable application release without installing it
- reserve `synctify update` for the music-library workflow
- change automatic release prompts and notices to say `application` and `upgrade` explicitly
- resume the original command after an accepted automatic application upgrade using the existing installed-wrapper handoff
- update packaged CLI, macOS bundle, and release-workflow smoke tests to require `upgrade` and reject the removed `self-update` command

No SQLite schema migration is required for this release.

## 1.1.0 - 2026-08-23

Synctify 1.1.0 turns the Textual TUI into an operational frontend for existing CLI workflows.

### TUI command center

- add dashboard actions and keyboard shortcuts for Spotify import and the coordinated update workflow
- add a Commands tab for non-interactive Synctify commands
- stream merged CLI stdout/stderr into the TUI while keeping command execution off the UI thread
- preserve the active Synctify home and canonical library path for child commands
- reload normal TUI settings after config changes
- render child output as literal text instead of Rich markup
- terminate and reap a child command if output handling aborts while the TUI is shutting down

No SQLite schema migration is required for this release.

## 1.0.6 - 2026-08-23

Synctify 1.0.6 makes coordinated updates visibly report what they are doing instead of appearing frozen during long Spotify and Streamrip operations.

### Update progress

- report Spotify, resolution, download, playlist-readiness, and playlist-build stages
- send progress messages to stderr while keeping the final structured update report on stdout
- preserve dry-run rollback behavior

No SQLite schema migration is required for this release.

## 1.0.5 - 2026-08-22

Synctify 1.0.5 adds release-aware self-updating on top of the stable macOS installation layout.

### Self-update and prompt policy

- add explicit stable-release checking/installing
- use authenticated GitHub API access when configured
- validate release tags, assets, archive layout, VERSION metadata, and available asset digests
- check for newer releases on installed-command invocations according to `auto-update`
- keep update-check failures non-fatal and non-interactive scripts non-blocking

No SQLite schema migration is required for this release.

## 1.0.4 - 2026-08-22

Synctify 1.0.4 adds a stable macOS installation layout.

### Stable macOS installation

- install versioned application payloads under `app/releases/<version>`
- maintain `app/current` as the active release pointer
- install a stable `~/.local/bin/synctify` wrapper
- keep each release's private Python environment isolated from user data
- include and smoke-test the installer in macOS release bundles

No SQLite schema migration is required for this release.

## 1.0.3 - 2026-08-22

Synctify 1.0.3 adds first-class configuration for the canonical local FLAC library directory.

### Configurable canonical library

- prompt for the library directory during setup
- add scripted setup flags, persistent configuration, and `SYNCTIFY_LIBRARY_DIR`
- make CLI, TUI, audit, acquisition, playlist, and backup paths use the configured library
- keep path changes non-destructive

No SQLite schema migration is required for this release.

## 1.0.2 - 2026-08-22

Synctify 1.0.2 aligns package and release metadata around the macOS launcher bundle.

### Release packaging

- publish the macOS ZIP as the primary end-user artifact
- bundle the matching Python wheel and launcher inside the ZIP
- distinguish GitHub source archives from the end-user bundle

No SQLite schema or production workflow behavior changes are included in this patch.

## 1.0.1 - 2026-08-22

Synctify 1.0.1 adds the first end-user macOS distribution layer.

### macOS distribution

- add an executable macOS launcher ZIP
- create/reuse a private `.venv` in the extracted bundle
- launch the TUI with no arguments and forward normal CLI arguments
- test the bundle across supported Python versions

No SQLite schema migration is required for this release.

## 1.0.0 - 2026-08-22

Synctify 1.0.0 is the first stable release of the canonical lossless-library workflow.

### Safety and correctness

- preserve temporarily inaccessible Spotify playlists
- prevent garbage collection from following recorded symlinks
- keep mirrors and relinking safe across path and assignment edge cases
- reject weak or grossly mismatched automatic track matches

### Lossless source policy

- use Qobuz -> Tidal -> Deezer as the automatic fallback chain
- retire SoundCloud from automatic FLAC resolution/acquisition
- reject MP3-only qobuz-dl quality settings for new runtime configuration

### Migration and state handling

- add portable setup export/import, FLAC relinking, and resumable migration checkpoints
- keep preview/update/status operations from mutating state unexpectedly

### Scale and performance

- bound fallback selection in the database
- avoid SQLite variable-limit failures
- reuse FLAC metadata indexes during acquisition reconciliation

### CLI and TUI

- compose the installed Typer CLI explicitly from `synctify.app:app`
- add a Textual TUI for dashboard, unresolved-track review, Doctor, and Audit
- keep long diagnostic work off the UI thread

### Documentation and release validation

- document the supported lossless workflow, configuration, migration, audit, mirrors, backups, and TUI
- validate built packages on macOS across supported Python versions
