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

Spotify discovery and music-library updates are deliberately separate.

Fetch the playlist list, including Liked Songs, without fetching every song:

   synctify spotify fetch-playlists

In the TUI open the Spotify tab. Select one playlist at a time, choose
Load / Review, include or exclude individual tracks, and then Confirm Playlist.
Liked Songs behaves like a normal playlist. Exclusions persist.

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
It resolves confirmed tracks, acquires missing FLACs, and rebuilds playlists.
Long operations report their stage to stderr while the final structured report
remains on stdout.

TUI activity and cancellation
-----------------------------

Long-running TUI actions show a loading indicator and status message. Conflicting
buttons are disabled while an action is running. Press Esc or choose Cancel (Esc)
to cancel an active cancellable operation. The Spotify tab shows included,
excluded, pending-addition, and pending-removal counts for each playlist.

Application upgrades
--------------------

Updating the Synctify application is separate from `synctify update`:

   synctify upgrade --check
   synctify upgrade

Installed Synctify checks for a newer stable application release according to
the configured auto-update policy. The default interactive prompt looks like:

   Synctify application 1.2.1 is available (current: 1.2.0). Upgrade now? [Y/n]

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

Synctify 1.2 uses database schema v6 for the staged Spotify playlist catalog,
persistent exclusions, and pending playlist changes. Existing imported Spotify
playlists are migrated into the new tracked catalog when the schema is upgraded.
