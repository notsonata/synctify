#!/bin/bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VERSION_FILE="$ROOT_DIR/VERSION"
INSTALLER="$ROOT_DIR/install.sh"

fail() {
  printf 'synctify: %s\n' "$*" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "this release bundle targets macOS"
fi

if [[ "${1:-}" == "install" ]]; then
  [[ -x "$INSTALLER" ]] || fail "missing executable install.sh next to synctify.sh"
  shift
  exec "$INSTALLER" "$@"
fi

[[ -f "$VERSION_FILE" ]] || fail "missing VERSION file next to synctify.sh"
VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"
[[ -n "$VERSION" ]] || fail "VERSION file is empty"

shopt -s nullglob
WHEELS=("$ROOT_DIR"/synctify-*.whl)
shopt -u nullglob
[[ ${#WHEELS[@]} -eq 1 ]] || fail "expected exactly one synctify wheel next to synctify.sh"
WHEEL="${WHEELS[0]}"

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

resolve_python() {
  local candidate resolved
  local candidates=("${SYNCTIFY_PYTHON:-}" python3.14 python3.13 python3.12 python3)
  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if [[ "$candidate" == */* ]]; then
      [[ -x "$candidate" ]] || continue
      resolved="$candidate"
    else
      resolved="$(command -v "$candidate" 2>/dev/null || true)"
      [[ -n "$resolved" ]] || continue
    fi
    if python_is_supported "$resolved"; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(resolve_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  fail "Python 3.12 or newer is required. Install it, or set SYNCTIFY_PYTHON to a compatible interpreter."
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  printf 'Creating Synctify environment with %s...\n' "$PYTHON_BIN"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

installed_version="$(
  "$VENV_DIR/bin/python" -c 'import synctify; print(synctify.__version__)' 2>/dev/null || true
)"

if [[ "$installed_version" != "$VERSION" ]]; then
  printf 'Installing Synctify %s into the private environment...\n' "$VERSION"
  "$VENV_DIR/bin/python" -m pip --disable-pip-version-check install --upgrade "$WHEEL"
fi

if [[ $# -eq 0 ]]; then
  exec "$VENV_DIR/bin/synctify" tui
fi

exec "$VENV_DIR/bin/synctify" "$@"
