#!/usr/bin/env sh
set -eu

SKILL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SKILL_ROOT"

if [ ! -f clawsqlite.toml ]; then
  if command -v clawsqlite >/dev/null 2>&1; then
    clawsqlite knowledge init-config --out clawsqlite.toml
  elif [ -f ../../clawsqlite_cli.py ]; then
    python3 -m clawsqlite_cli knowledge init-config --out clawsqlite.toml
  else
    cat >&2 <<'EOF'
ERROR: clawsqlite CLI was not found.
NEXT: install clawsqlite, then rerun this bootstrap from the skill directory.
EOF
    exit 2
  fi
fi

cat <<EOF
clawsqlite-knowledge bootstrap complete.
Skill root: $SKILL_ROOT
Config: $SKILL_ROOT/clawsqlite.toml

Edit clawsqlite.toml with real [knowledge], [llm], [embedding], and [scraper]
values before running strict ingest.
EOF
