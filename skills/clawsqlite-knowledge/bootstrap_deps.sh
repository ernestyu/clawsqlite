#!/usr/bin/env sh
set -eu

if [ "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN=""
fi

if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 was not found." >&2
  echo "NEXT: install Python 3.10+ or set PYTHON=/path/to/python." >&2
  exit 2
fi

"$PYTHON_BIN" -m pip install --upgrade clawsqlite

if ! command -v clawsqlite >/dev/null 2>&1; then
  echo "ERROR: clawsqlite console script was not found after installation." >&2
  echo "NEXT: ensure the Python scripts directory is on PATH, then rerun bootstrap_deps.sh." >&2
  exit 2
fi

clawsqlite --help >/dev/null
clawsqlite knowledge --help >/dev/null

cat <<'EOF'
clawsqlite-knowledge dependencies installed.

Next steps:
1. Create or edit ./clawsqlite.toml in this skill/component directory.
2. Validate with:
   clawsqlite knowledge maintenance doctor --json
EOF
