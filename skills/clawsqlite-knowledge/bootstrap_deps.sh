#!/usr/bin/env sh
set -eu

BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOCAL_TARGET="$BASE_DIR/.clawsqlite-python"

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

INSTALL_MODE="environment"
if ! "$PYTHON_BIN" -m pip install --upgrade clawsqlite; then
  echo "WARN: environment install failed; falling back to local skill dependency target." >&2
  rm -rf "$LOCAL_TARGET"
  "$PYTHON_BIN" -m pip install --upgrade --target "$LOCAL_TARGET" clawsqlite
  PYTHONPATH="$LOCAL_TARGET${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONPATH
  INSTALL_MODE="local-target"
fi

run_clawsqlite() {
  if [ "$INSTALL_MODE" = "local-target" ]; then
    "$PYTHON_BIN" -m clawsqlite_cli "$@"
  elif command -v clawsqlite >/dev/null 2>&1; then
    clawsqlite "$@"
  else
    "$PYTHON_BIN" -m clawsqlite_cli "$@"
  fi
}

clawsqlite_command_text() {
  if [ "$INSTALL_MODE" = "local-target" ]; then
    printf 'PYTHONPATH="%s" %s -m clawsqlite_cli' "$PYTHONPATH" "$PYTHON_BIN"
  elif command -v clawsqlite >/dev/null 2>&1; then
    printf 'clawsqlite'
  else
    printf '%s -m clawsqlite_cli' "$PYTHON_BIN"
  fi
}

if ! run_clawsqlite --help >/dev/null 2>&1; then
  echo "ERROR: installed clawsqlite package could not be imported or executed." >&2
  echo "NEXT: check Python/pip output above, then rerun bootstrap_deps.sh." >&2
  exit 2
fi

run_clawsqlite knowledge --help >/dev/null

cat <<'EOF'
clawsqlite-knowledge dependencies installed.
EOF

CLAWSQLITE_CMD=$(clawsqlite_command_text)

if [ "$CLAWSQLITE_CMD" = "clawsqlite" ]; then
  cat <<'EOF'

CLI command:
  clawsqlite
EOF
else
  cat <<EOF

CLI command:
  $CLAWSQLITE_CMD

NOTE: the clawsqlite console script is not on PATH. The package was validated
through Python module execution instead.
EOF
fi

if [ "$INSTALL_MODE" = "local-target" ]; then
  cat <<EOF

Local dependency target:
  $LOCAL_TARGET
EOF
fi

cat <<'EOF'
Next steps:
1. Create the default knowledge instance:
EOF
cat <<EOF
   $CLAWSQLITE_CMD knowledge maintenance init-config --instance default
EOF
cat <<'EOF'
2. Enter the knowledge instance home:
   cd ~/.openclaw/workspace/data/clawsqlite-knowledge/default
EOF
cat <<EOF
3. Validate with:
   $CLAWSQLITE_CMD knowledge maintenance doctor --json
EOF
