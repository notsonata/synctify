# Synctify

Synctify is a local-first macOS music library manager that uses Spotify as playlist/library state, keeps one canonical local lossless library, generates UTF-8 M3U8 playlists, mirrors that library to devices, and can maintain a non-destructive cloud backup.

## Intended workflow

```text
Spotify
   ↓
Desired playlist/library state
   ↓
Track matching
   ↓
Acquisition provider
   ↓
Canonical local library
   ↓
M3U8 playlists
   ├── mirror → phone / external drive
   └── backup → pCloud
```

The acquisition layer is intentionally provider-agnostic. Qobuz tooling can be integrated behind an adapter without making playlist state, matching, or sync logic depend on one downloader.

## Sync semantics

Synctify treats device synchronization and cloud backup as different operations:

- **Mirror** targets use source-of-truth semantics. Files removed locally are removed from the destination on the next sync. This is intended for phones and external drives.
- **Backup** targets are append/update-only. Remote-only files are never deleted just because they were deleted locally. This is intended for pCloud.
- Playlist backup snapshots will be versioned separately so historical M3U8 states can be retained.

## Current state

The initial foundation includes:

- Python 3.12 package and `synctify` CLI
- macOS-friendly application data paths
- SQLite schema for tracks, Spotify/Qobuz mappings, playlists, sync targets, and sync runs
- deterministic UTF-8 M3U8 generation using relative paths
- explicit mirror vs backup sync policy
- rclone command planning for filesystem/cloud targets
- tests and macOS GitHub Actions CI

Spotify ingestion, track matching, acquisition adapters, device transport, and pCloud playlist snapshots are the next implementation stages.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

Initialize local state:

```bash
synctify init
synctify status
```

Set `SYNCTIFY_HOME` to override the default application data directory during development or testing.

## Planned commands

```text
synctify init
synctify status
synctify update
synctify resolve
synctify playlists build
synctify sync <target>
synctify backup <target>
synctify clean --dry-run
```
