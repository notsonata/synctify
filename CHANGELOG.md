# Changelog

## 1.1.1 - 2026-08-23

Synctify 1.1.1 separates application upgrades from music-library updates so the two operations no longer share confusing terminology.

### Upgrade command

- replace the public `synctify self-update` command with `synctify upgrade`
- keep `synctify upgrade --check` for checking the latest stable application release without installing it
- reserve `synctify update` exclusively for the Spotify, resolution, acquisition, and playlist workflow
- change automatic release prompts and notices to say `application` and `upgrade` explicitly
- resume the original command after an accepted automatic application upgrade using the existing installed-wrapper handoff
- update packaged CLI, macOS bundle, and release-workflow smoke tests to require `upgrade` and reject the removed `self-update` command

No SQLite schema migration is required for this release.

## 1.1.0 - 2026-08-23

Synctify 1.1.0 turns the Textual TUI into an operational frontend for the existing CLI workflows instead of limiting it to local state and diagnostics.

### TUI command center

- add an `Import Spotify` dashboard action for the existing `synctify spotify pull` desired-state import
- add a `Full Update` action for the coordinated Spotify, resolution, acquisition, and playlist workflow
- add keyboard shortcuts `i` for Spotify import and `u` for full update
- add a fifth Commands tab with quick actions for Spotify import, full update, automatic resolution, playlist building, and target listing
- add a command input for non-interactive Synctify commands such as acquisition, sync, backup, and config operations
- stream merged CLI stdout/stderr into the TUI while keeping command execution off the UI thread
- preserve the active Synctify home and canonical library path for child commands
- reload normal TUI settings after commands so `config set library-dir` and `config unset library-dir` take effect without restarting
- render child output as literal text instead of Rich markup so bracketed progress labels and filenames are not interpreted as formatting
- terminate and reap a child command if output handling aborts while the TUI is shutting down
- keep recursive TUI launches blocked and keep prompt-driven commands in Terminal

### Release integration

- retain the 1.0.6 coordinated-update progress reporting when rebasing the TUI work onto current `main`
- add regression coverage for command composition, streamed output, process cleanup, settings propagation, and Commands-tab navigation

No SQLite schema migration is required for this release.

## 1.0.6 - 2026-08-23

Synctify 1.0.6 makes coordinated updates visibly report what they are doing instead of appearing frozen during long Spotify and Streamrip operations.

### Update progress

- print an immediate status line while fetching Spotify desired state
- report the number of unresolved tracks being searched for each automatic-resolution source
- report download planning, acquisition groups, playlist readiness checks, and playlist rebuilding
- send progress messages to stderr while keeping the final structured update report on stdout
- preserve dry-run rollback behavior so interrupted previews do not persist Spotify or resolution state

No SQLite schema migration is required for this release.

## 1.0.5 - 2026-08-22

Synctify 1.0.5 adds release-aware self-updating on top of the stable macOS installation layout introduced in 1.0.4.

### Self-update

- add `synctify self-update --check` to inspect the latest stable GitHub Release without installing it
- add `synctify self-update` to download, validate, extract, and install a newer macOS release bundle
- use authenticated GitHub API access when `SYNCTIFY_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, or an authenticated `gh` CLI session is available
- validate the stable release tag, expected `synctify-<version>-macos.zip` asset, archive paths, bundle VERSION, and optional GitHub SHA-256 asset digest before installation
- reuse the 1.0.4 versioned `app/releases/<version>` layout and switch `app/current` only through the existing installer
- restart the original command through the stable `synctify` wrapper after an automatic update

### Update prompt policy

- check GitHub for a newer stable release on every invocation of the installed `synctify` command
- default `auto-update` to `prompt`: interactive terminals ask `Update now? [Y/n]` before installing
- never block non-interactive scripts for input; they print an update notice and continue
- support `auto-update` values `prompt`, `check`, `install`, and `off`, with the `SYNCTIFY_AUTO_UPDATE` environment override
- keep portable release bundles and development checkouts free of automatic update checks
- keep update-check failures non-fatal so offline GitHub access cannot prevent normal Synctify commands from running

No SQLite schema migration is required for this release.

## 1.0.4 - 2026-08-22

Synctify 1.0.4 adds a stable macOS installation layout so users can run `synctify` from anywhere instead of keeping Terminal inside an extracted release folder.

### Stable macOS installation

- add `./synctify.sh install` to install the release bundle into Synctify's application-data area
- store application payloads in versioned `app/releases/<version>` directories
- maintain an `app/current` symlink that selects the active application release
- install a stable `~/.local/bin/synctify` wrapper that always launches `app/current/synctify.sh`
- add `~/.local/bin` to the user's shell profile when required, while keeping the extracted bundle usable in portable mode
- keep each installed release's private `.venv` isolated inside that versioned application payload
- preserve database, configuration, playlists, and canonical library paths separately from the replaceable application payload
- establish the versioned/current layout needed for a future atomic self-update implementation

### Distribution validation

- include the executable installer in the macOS release ZIP
- smoke-test the stable installed command in normal CI and the tag-triggered release workflow
- add installer coverage for the versioned app layout, `current` symlink, PATH setup, and argument forwarding

No SQLite schema migration is required for this release.

## 1.0.3 - 2026-08-22

Synctify 1.0.3 adds first-class configuration for the canonical local FLAC library directory and aligns release metadata for the updated macOS bundle.

### Configurable canonical library

- prompt for the canonical music library directory during interactive setup
- add `--library` / `--library-dir` to scripted setup
- add persistent `library-dir` configuration and the `SYNCTIFY_LIBRARY_DIR` environment override
- make CLI, TUI, audit, acquisition, playlist, and backup runtime paths use the configured canonical library
- avoid creating the default application-data `library` directory when setup selects a custom location
- keep changing the canonical path non-destructive: existing files are not moved and existing database `local_path` values are not silently rewritten
- document changing and unsetting the library override in the macOS bundle and main README

### Release metadata

- publish `synctify-1.0.3-macos.zip` and `synctify-1.0.3.tar.gz` from synchronized package/runtime/documentation metadata
- document the project rule that feature and fix updates must include a matching version bump and changelog update

No SQLite schema migration is required for this release.

## 1.0.2 - 2026-08-22

Synctify 1.0.2 is the current macOS distribution release, carrying forward the 1.0.1 launcher bundle and the release-asset cleanup that followed it.

### Release packaging

- publish `synctify-1.0.2-macos.zip` as the primary end-user download
- keep the executable `synctify.sh`, matching Synctify wheel, `VERSION`, and setup instructions inside the ZIP
- publish only the macOS ZIP and `synctify-1.0.2.tar.gz` as authored GitHub Release assets
- keep the Python wheel bundled inside the macOS ZIP instead of exposing it as a separate public asset
- distinguish GitHub's automatically generated Source code archives from the end-user launcher bundle in the README
- align package, runtime, documentation, CI smoke tests, and tag verification on version 1.0.2

No SQLite schema or production workflow behavior changes are included in this patch.

## 1.0.1 - 2026-08-22

Synctify 1.0.1 adds the first end-user macOS distribution layer on top of the verified Python package release.

### macOS distribution

- add `synctify-1.0.1-macos.zip` as a first-class GitHub Release asset
- include an executable `synctify.sh`, the matching Synctify wheel, `VERSION`, and concise setup instructions
- make `./synctify.sh` create and reuse a private `.venv` inside the extracted release folder
- launch the Textual TUI when the launcher is run without arguments, while forwarding supplied arguments to the normal CLI
- auto-detect Python 3.12+ or honor `SYNCTIFY_PYTHON` for an explicit interpreter
- keep Synctify state, configuration, and the canonical library outside the disposable release folder
- build and execute the ZIP launcher in CI on Python 3.12, 3.13, and 3.14
- smoke-test the same ZIP again in the tag-triggered release workflow before publishing it
- include the ZIP in `SHA256SUMS` alongside the wheel and source distribution

The launcher still relies on internet access during its first bootstrap to install Python dependencies from the bundled wheel metadata. Streamrip, qobuz-dl, and rclone remain external tools and are not vendored in the ZIP.

## 1.0.0 - 2026-08-22

Synctify 1.0.0 is the first stable release of the canonical lossless-library workflow, incorporating the correctness, migration, performance, source-policy, CLI architecture, and interactive TUI work completed during the pre-1.0 development series.

### Safety and correctness

- preserve temporarily inaccessible Spotify playlists instead of deleting desired state
- prevent garbage collection from following recorded symlinks to referenced audio targets
- make filesystem mirror destinations absolute and stable across working directories
- keep relink source assignments unique across limited/resumed runs
- make generated-playlist naming safe for case-insensitive macOS filesystems
- requeue acquisition when a manual resolution changes and when recorded audio disappears
- reject gross duration mismatches and weak title/artist-only automatic matches
- normalize persisted provider names and reject unsupported resolution providers

### Lossless source policy

- canonical automatic fallback is Qobuz -> Tidal -> Deezer
- SoundCloud is retired from automatic resolution/acquisition into the FLAC library
- qobuz-dl quality 5 (MP3 320) is rejected for new runtime settings
- older SoundCloud/quality-5 configuration remains readable and is normalized safely at runtime
- legacy unsupported resolution rows are retired so affected tracks can enter supported fallback again

### Migration and state handling

- add portable setup export/import
- add FLAC relinking for copied libraries
- add coordinated migration bootstrap and resumable migration checkpoints
- allow completed relink stages to resume after the old removable source disappears
- harden checkpoint error persistence and atomic writes
- keep preview/update/status operations from mutating state unexpectedly

### Scale and performance

- bound fallback selection with chunked database-side filtering
- avoid SQLite variable-limit failures for large fallback sets
- reuse one FLAC metadata index per acquisition batch during existing-file reconciliation
- report each track's final fallback outcome instead of an intermediate source result

### CLI and maintainability

- compose the installed Typer CLI explicitly from `synctify.app:app`
- stop relying on import-order command replacement for the installed command surface
- lock top-level and nested command names with regression tests
- retain legacy command modules as internal compatibility surfaces

### Interactive TUI

- add `synctify tui` using Textual as an interactive frontend while preserving the normal CLI
- show dashboard counts for desired/local/unresolved tracks, resolutions, pending downloads, playlists, targets, and Spotify pull state
- add unresolved-track review with manual Qobuz/Tidal/Deezer mapping through the core resolution layer
- expose Doctor and read-only Audit reports in tabular views
- keep long Doctor/Audit work off the UI thread with managed Textual workers
- keep TUI launch read-only with respect to initialization and schema migration

### Documentation and release validation

- rewrite the README around the current supported lossless workflow
- remove stale SoundCloud setup/acquisition guidance
- document configuration precedence, migration, relinking, audit, mirrors, backups, the TUI, and the explicit CLI composition model
- validate the built wheel on macOS with Python 3.12, 3.13, and 3.14 before release
