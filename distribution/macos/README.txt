Synctify macOS release bundle
============================

Quick start
-----------

1. Extract the release ZIP.
2. In Terminal, enter the extracted Synctify folder.
3. Run first-time setup:

   ./synctify.sh setup

   Setup prompts for the canonical music library directory. Press Enter to use
   the normal Synctify application-data library, or enter an absolute path to
   an existing/local FLAC library.

4. Launch the interactive TUI:

   ./synctify.sh

With no arguments, synctify.sh launches the TUI.
You can also use every normal CLI command through the same launcher, for example:

   ./synctify.sh doctor
   ./synctify.sh update --dry-run
   ./synctify.sh update

To change the canonical library later:

   ./synctify.sh config set library-dir /absolute/path/to/Music

Remove that override to return to the built-in location:

   ./synctify.sh config unset library-dir

If macOS or your unzip tool removed the executable bit, run this once:

   chmod +x synctify.sh

Requirements
------------

- macOS
- Python 3.12 or newer
- Internet access on the first run so pip can install the bundled wheel's Python dependencies
- A Spotify developer application for Spotify import/login features
- Streamrip for automatic catalog resolution and Tidal/Deezer acquisition
- qobuz-dl for the default Qobuz acquisition path
- rclone for mirror/backup operations

The external download/sync tools are not bundled with Synctify.

What the launcher does
----------------------

synctify.sh finds Python 3.12+, creates a private .venv inside this extracted folder, and installs the Synctify wheel shipped in the same ZIP. Later launches reuse that environment. When you replace this folder with a newer release, its launcher will bootstrap the matching version again.

Set SYNCTIFY_PYTHON to an explicit Python interpreter if you do not want the launcher to auto-detect one.

Your data
---------

Synctify's database, configuration, canonical music library, and playlists are NOT stored in this release folder. The database, configuration, and generated playlists continue to live in Synctify's normal macOS application-data directory (or SYNCTIFY_HOME if you set it). The canonical music library uses that location by default, but can be pointed at another absolute directory during setup, with `config set library-dir`, or via SYNCTIFY_LIBRARY_DIR.

Deleting or replacing this extracted release folder therefore does not delete your Synctify state or music library. The .venv inside the release folder is disposable.

Checksums
---------

The GitHub Release also includes SHA256SUMS. To verify only the downloaded macOS ZIP, place the ZIP and SHA256SUMS in the same directory and run:

   grep -- '-macos.zip$' SHA256SUMS | shasum -a 256 -c -
