#!/usr/bin/env bash
# Cross-platform Python launcher for graphify-zipper scripts.
#
# Detects a working Python >= 3.10 interpreter and caches its path in
# `.python_bin` next to this script. Subsequent invocations skip detection.
#
# Usage:
#   bash pyrun.sh <script.py> [args...]
#
# Detection order: cached .python_bin → python3 → python → py -3.
# Requires Python 3.10+ (codebase uses PEP-604 `dict | None` syntax).

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$DIR/.python_bin"

check_py() {
  $* -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >/dev/null 2>&1
}

PY=""
if [ -f "$CACHE" ]; then
  PY="$(cat "$CACHE")"
  check_py $PY || PY=""
fi

if [ -z "$PY" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && check_py "$cand"; then
      PY="$cand"
      break
    fi
  done
  if [ -z "$PY" ] && command -v py >/dev/null 2>&1 && check_py py -3; then
    PY="py -3"
  fi
  if [ -z "$PY" ]; then
    echo "graphify-zipper: no Python 3.10+ interpreter found" >&2
    echo "Tried: python3, python, py -3" >&2
    exit 2
  fi
  echo "$PY" > "$CACHE"
fi

SCRIPT="$1"; shift
exec $PY "$DIR/$SCRIPT" "$@"
