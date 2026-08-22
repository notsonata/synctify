# Changelog

## 0.23.0 - 2026-08-22

Synctify 0.23.0 is a correctness, migration, and maintainability release built around the canonical lossless-library workflow.

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

### Documentation

- rewrite the README around the current supported lossless workflow
- remove stale SoundCloud setup/acquisition guidance
- document configuration precedence, migration, relinking, audit, mirrors, backups, and the explicit CLI composition model
