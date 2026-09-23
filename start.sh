#!/usr/bin/env sh
# Decision Router: creates .venv on first run, then opens the dashboard.
# Extra arguments are passed to "serve", e.g. ./start.sh --port 9000
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  for candidate in python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo "Python 3.12 or newer is required: https://www.python.org/downloads/" >&2
    exit 1
  fi
  echo "[1/2] Creating the Python environment (.venv)..."
  "$PYTHON" -m venv .venv
fi

if ! .venv/bin/python -c "import decision_router.dashboard" >/dev/null 2>&1; then
  echo "[2/2] Installing dependencies (first run only)..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -e .
fi

exec .venv/bin/python -m decision_router.cli serve --open "$@"
