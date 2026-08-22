Synctify macOS release bundle
============================

Quick start
-----------

1. Extract the release ZIP.
2. In Terminal, enter the extracted Synctify folder.
3. Install the stable command:

   ./synctify.sh install

   The installer copies this release into Synctify's application-data area,
   creates ~/.local/bin/synctify, and adds ~/.local/bin to your shell PATH when
   needed. If PATH was changed, restart Terminal or source the profile printed
   by the installer.

4. Run first-time setup:

   synctify setup

5. Launch the interactive TUI:

   synctify

Spotify workflow
----------------

Spotify discovery/review and provider downloads are deliberately separate.

Fetch the playlist list, including Liked Songs, without fetching every song:

   synctify spotify fetch-playlists

In the TUI open the Spotify tab. Select one playlist at a time, choose
Load / Review, include or exclude individual tracks, and then Confirm Playlist.
Liked Songs behaves like a normal playlist. Exclusions persist.

Fetching, loading, reviewing, and confirming Spotify playlists do not contact
Qobuz, Tidal, or Deezer and do not start downloads. Confirmation only records
the user's approved desired state.

Later, check only playlists already imported into Synctify:

   synctify spotify update-tracked

New tracks are shown as pending additions. Tracks removed on Spotify are shown
as pending removals. Neither kind of change enters the active library until the
user reviews it and chooses Apply Choices.

Unimporting a playlist removes its local generated playlist. Local FLACs are
removed only after their last imported-playlist reference is gone. If the same
track is still used by another imported playlist, its FLAC remains.

Cloud backups are never deleted by unimport. rclone backup operations use
copy-only semantics for the music library and playlist backup paths, so existing
remote files and historical snapshots remain intact.

Library update
--------------

   synctify update --dry-run
   synctify update

`synctify update` works only on playlists/tracks already confirmed in Synctify.
It does not fetch Spotify playlists or automatically accept new Spotify tracks.
The Dashboard counts only this active imported desired state; historical rows
kept for migration/review do not inflate the active track or unresolved totals.

Before any provider lookup, Synctify validates recorded local paths against the
Spotify track identity. Unrecorded canonical FLACs are auto-adopted only when
there is one unique exact ISRC match. Automatic Library Update no longer uses
fuzzy metadata-only local matching. A rejected database association never
deletes the FLAC; the track simply returns to the missing/resolution queue.

Only still-missing approved tracks proceed to Qobuz/Tidal/Deezer resolution and
acquisition. Resolver progress includes the owning imported playlist so the
active scope is visible, for example:

   Resolving via deezer [18/223] Liked Songs · Cage The Elephant - Trouble

If provider lookup leaves some approved tracks unresolved, the update completes
with an incomplete-playlist warning instead of status 2. Status 2 is reserved
for actual provider/search/download failures.

Tidal authentication is never started automatically from a Synctify batch. If
Streamrip's saved Tidal session is missing or expired, Synctify skips Tidal once
and continues to the next configured fallback instead of opening repeated login
pages. Authenticate or refresh Tidal manually in Terminal first:

   rip config --tidal

TUI activity and cancellation
-----------------------------

Long-running TUI actions show a loading indicator and status message. During
automatic resolution, the bottom operation strip shows a determinate progress
bar with source, current/total count, owning playlist, and current track.
Starting Library Update from the dashboard stays on the current tab instead of
forcing Commands open. Conflicting buttons are disabled while an action is
running. Press Esc or choose Cancel (Esc) to cancel an active cancellable
operation. The Spotify tab shows included, excluded, pending-addition, and
pending-removal counts for each playlist.

Application upgrades
--------------------

Updating the Synctify application is separate from `synctify update`:

   synctify upgrade --check
   synctify upgrade

Installed Synctify checks for a newer stable application release according to
the configured auto-update policy. The default interactive prompt looks like:

   Synctify application 1.2.4 is available (current: 1.2.3). Upgrade now? [Y/n]

Upgrade policy:

   synctify config set auto-update prompt
   synctify config set auto-update check
   synctify config set auto-update install
   synctify config set auto-update off

SYNCTIFY_AUTO_UPDATE can override the saved value. For private releases,
authenticate GitHub CLI with `gh auth login` or set SYNCTIFY_GITHUB_TOKEN.
GH_TOKEN and GITHUB_TOKEN are also recognized.

Other commands
--------------

   synctify doctor
   synctify resolve status
   synctify acquire --dry-run
   synctify playlists build
   synctify backup cloud --dry-run

To change the canonical library later:

   synctify config set library-dir /absolute/path/to/Music

Remove that override to return to the built-in location:

   synctify config unset library-dir

The extracted bundle can also be used portably without installation:

   ./synctify.sh
   ./synctify.sh doctor

Portable runs do not perform automatic application-upgrade checks.

If macOS or your unzip tool removed the executable bit:

   chmod +x synctify.sh install.sh

Requirements
------------

- macOS
- Python 3.12 or newer
- Internet access on first application launch so pip can install dependencies
- Spotify developer application for playlist discovery/login
- Streamrip for automatic catalog resolution and Tidal/Deezer acquisition
- qobuz-dl for the default Qobuz acquisition path
- rclone for mirror/backup operations

The external download/sync tools are not bundled with Synctify.

What installation does
----------------------

The installer stores each application release under:

   ~/Library/Application Support/Synctify/app/releases/<version>

It switches this stable pointer to the installed release:

   ~/Library/Application Support/Synctify/app/current

The ~/.local/bin/synctify wrapper always launches app/current/synctify.sh. Each
installed release creates and reuses its own private .venv.

Your data
---------

Synctify's database, configuration, canonical music library, and playlists are
NOT stored in this release folder. They remain in Synctify's normal application
data locations (or SYNCTIFY_HOME if overridden). Replacing an application
release therefore does not delete user data.

Synctify 1.2.3 keeps database schema v7. No new migration is required. Schema v7
still keeps previously auto-approved legacy playlists in review state until the
user explicitly confirms them. Track rows, local audio files, provider mappings,
and cloud backups remain preserved.
