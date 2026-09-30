#!/bin/sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Wymagany jest python3.' >&2; exit 127; }
exec python3 "$DIR/c_force_installer.py" uninstall "$@"
