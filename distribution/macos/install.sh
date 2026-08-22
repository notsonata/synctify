#!/bin/bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"
APP_ROOT="${SYNCTIFY_APP_ROOT:-$HOME/Library/Application Support/Synctify/app}"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
BIN_DIR="${SYNCTIFY_BIN_DIR:-$HOME/.local/bin}"
COMMAND_PATH="$BIN_DIR/synctify"

fail() {
  printf 'synctify install: %s\n' "$*" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "this installer targets macOS"
fi

[[ -f "$VERSION_FILE" ]] || fail "missing VERSION file next to install.sh"
VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"
[[ -n "$VERSION" ]] || fail "VERSION file is empty"
[[ -x "$ROOT_DIR/synctify.sh" ]] || fail "missing executable synctify.sh next to install.sh"

shopt -s nullglob
WHEELS=("$ROOT_DIR"/synctify-*.whl)
shopt -u nullglob
[[ ${#WHEELS[@]} -eq 1 ]] || fail "expected exactly one synctify wheel next to install.sh"
WHEEL="${WHEELS[0]}"

INSTALL_DIR="$RELEASES_DIR/$VERSION"
STAGING_DIR="$APP_ROOT/.install-$VERSION-$$"

if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
  fail "$CURRENT_LINK exists and is not a symlink; refusing to replace it"
fi

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR" "$RELEASES_DIR" "$BIN_DIR"
trap 'rm -rf "$STAGING_DIR"' EXIT

cp "$ROOT_DIR/synctify.sh" "$STAGING_DIR/synctify.sh"
cp "$ROOT_DIR/install.sh" "$STAGING_DIR/install.sh"
cp "$ROOT_DIR/VERSION" "$STAGING_DIR/VERSION"
cp "$ROOT_DIR/README.txt" "$STAGING_DIR/README.txt"
cp "$WHEEL" "$STAGING_DIR/$(basename "$WHEEL")"
chmod +x "$STAGING_DIR/synctify.sh" "$STAGING_DIR/install.sh"

rm -rf "$INSTALL_DIR"
mv "$STAGING_DIR" "$INSTALL_DIR"
trap - EXIT

ln -sfn "releases/$VERSION" "$CURRENT_LINK"

cat > "$COMMAND_PATH" <<'EOF'
#!/bin/bash
set -euo pipefail
APP_ROOT="${SYNCTIFY_APP_ROOT:-$HOME/Library/Application Support/Synctify/app}"
LAUNCHER="$APP_ROOT/current/synctify.sh"
if [[ ! -x "$LAUNCHER" ]]; then
  printf 'synctify: installed launcher not found at %s\n' "$LAUNCHER" >&2
  exit 1
fi
exec "$LAUNCHER" "$@"
EOF
chmod +x "$COMMAND_PATH"

PROFILE_UPDATED=0
if [[ "$BIN_DIR" == "$HOME/.local/bin" ]]; then
  PROFILE="${SYNCTIFY_PROFILE:-}"
  if [[ -z "$PROFILE" ]]; then
    case "${SHELL:-}" in
      */zsh) PROFILE="${ZDOTDIR:-$HOME}/.zprofile" ;;
      */bash) PROFILE="$HOME/.bash_profile" ;;
      *) PROFILE="$HOME/.profile" ;;
    esac
  fi
  mkdir -p "$(dirname "$PROFILE")"
  touch "$PROFILE"
  PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
  if ! grep -Fq "$PATH_LINE" "$PROFILE"; then
    printf '\n# Synctify command\n%s\n' "$PATH_LINE" >> "$PROFILE"
    PROFILE_UPDATED=1
  fi
fi

printf 'Installed Synctify %s\n' "$VERSION"
printf '  App:     %s\n' "$INSTALL_DIR"
printf '  Current: %s\n' "$CURRENT_LINK"
printf '  Command: %s\n' "$COMMAND_PATH"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  if [[ $PROFILE_UPDATED -eq 1 ]]; then
    printf '\nAdded %s to PATH in %s.\n' "$BIN_DIR" "$PROFILE"
    printf 'Restart Terminal or run: source %q\n' "$PROFILE"
  else
    printf '\n%s is not currently on PATH. Add it to your shell PATH, then restart the shell.\n' "$BIN_DIR"
  fi
else
  printf '\nThe `synctify` command is ready to use.\n'
fi
