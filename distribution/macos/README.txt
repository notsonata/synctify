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

   Setup prompts for the canonical music library directory. Press Enter to use
   the normal Synctify application-data library, or enter an absolute path to
   an existing/local FLAC library.

5. Launch the interactive TUI:

   synctify

The stable command can run every normal CLI command, for example:

   synctify doctor
   synctify update --dry-run
   synctify update

Installed Synctify checks for a newer stable release every time `synctify` is
run. When a newer release is available in an interactive Terminal, the default
behavior is:

   Synctify 1.0.6 is available (current: 1.0.5). Update now? [Y/n]

Answering yes downloads the matching macOS release bundle, validates it,
installs it into a new versioned app directory, switches app/current, and
restarts the command under the new version. Non-interactive scripts are never
blocked for input; they receive an update notice and continue.

Check or update explicitly at any time:

   synctify self-update --check
   synctify self-update

Update policy can be changed with:

   synctify config set auto-update prompt
   synctify config set auto-update check
   synctify config set auto-update install
   synctify config set auto-update off

`prompt` is the default. `check` only prints a notice, `install` updates without
asking, and `off` disables automatic checks. SYNCTIFY_AUTO_UPDATE can override
the saved value.

This repository may require GitHub authentication to read release assets. If it
is private, authenticate GitHub CLI with `gh auth login` or set
SYNCTIFY_GITHUB_TOKEN (GH_TOKEN and GITHUB_TOKEN are also recognized).

The extracted bundle can still be used portably without installation. Portable
runs do not perform automatic update checks:

   ./synctify.sh
   ./synctify.sh doctor

To change the canonical library later:

   synctify config set library-dir /absolute/path/to/Music

Remove that override to return to the built-in location:

   synctify config unset library-dir

If macOS or your unzip tool removed the executable bit, run this once:

   chmod +x synctify.sh install.sh

Requirements
------------

- macOS
- Python 3.12 or newer
- Internet access on first application launch so pip can install the bundled wheel's Python dependencies
- A Spotify developer application for Spotify import/login features
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

The ~/.local/bin/synctify wrapper always launches app/current/synctify.sh and
marks the process as an installed invocation. This keeps the command path stable
across releases and lets the updater install a complete version before switching
`current`.

Each installed release creates and reuses its own private .venv when first run.
Set SYNCTIFY_PYTHON to an explicit Python interpreter if you do not want the
launcher to auto-detect one.

Your data
---------

Synctify's database, configuration, canonical music library, and playlists are NOT stored in this release folder. The database, configuration, and generated playlists continue to live in Synctify's normal macOS application-data directory (or SYNCTIFY_HOME if you set it). The canonical music library uses that location by default, but can be pointed at another absolute directory during setup, with `config set library-dir`, or via SYNCTIFY_LIBRARY_DIR.

The installed application payload lives under the `app` subdirectory and is separate from those user-data paths. Replacing or removing an application release therefore does not delete the Synctify database, configuration, playlists, or an external canonical music library.
