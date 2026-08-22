Synctify macOS release bundle
============================

Quick start
-----------

1. Extract the release ZIP.
2. In Terminal, enter the extracted Synctify folder.
3. Run:

   ./synctify.sh

With no arguments, synctify.sh launches the interactive TUI.
You can also use every normal CLI command through the same launcher, for example:

   ./synctify.sh setup
   ./synctify.sh doctor
   ./synctify.sh update --dry-run
   ./synctify.sh update

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

Synctify's database, configuration, canonical music library, and playlists are NOT stored in this release folder. They continue to live in Synctify's normal macOS application-data directory (or SYNCTIFY_HOME if you set it).

Deleting or replacing this extracted release folder therefore does not delete your Synctify state or music library. The .venv inside the release folder is disposable.

Checksums
---------

The GitHub Release also includes SHA256SUMS. You can verify the downloaded ZIP from Terminal with:

   shasum -a 256 -c SHA256SUMS

That command expects SHA256SUMS and the downloaded release assets to be in the same directory.
