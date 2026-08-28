#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
cd "$script_dir"

if [ -x "$repo_root/.venv/bin/python" ]; then
  exec "$repo_root/.venv/bin/python" server.py
fi

if python3 - <<'PY' >/dev/null 2>&1
import flask
PY
then
  exec python3 server.py
fi

echo "Flask is not installed. Create .venv with: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
exit 1
