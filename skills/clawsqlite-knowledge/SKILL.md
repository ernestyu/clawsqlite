---
name: clawsqlite-knowledge
description: Knowledge base skill that uses the published clawsqlite CLI for ingest, search, show, and maintenance workflows.
version: 1.1.0
metadata: {"openclaw":{"homepage":"https://github.com/ernestyu/clawsqlite","tags":["knowledge","sqlite","search","cli"],"requires":{"bins":["python"],"env":[]},"install":[{"id":"clawsqlite_knowledge_bootstrap","kind":"bash","label":"Install clawsqlite Python package from PyPI","script":"set -e && cd {baseDir} && bash bootstrap_deps.sh"}]}}
---

# ClawSQLite Knowledge Skill

This skill is a thin wrapper around the published `clawsqlite` PyPI package.

It does not:

- vendor the `clawsqlite` source tree
- clone any Git repository
- define a second runtime layer on top of the official CLI
- redefine a parallel JSON API

It does:

- install `clawsqlite` from PyPI through `bootstrap_deps.sh`
- guide agents to use the official `clawsqlite knowledge ...` CLI
- document common workflows for knowledge-base operations

## Knowledge Instance Home

This skill directory is only the instruction shell. Do not store the user's
knowledge DB or private config here. Run `clawsqlite knowledge ...` from a
knowledge instance home instead, for example:

```bash
mkdir -p ~/.openclaw/workspace/data/clawsqlite-knowledge/default
cd ~/.openclaw/workspace/data/clawsqlite-knowledge/default
```

The local private config must be:

```text
./clawsqlite.toml
```

`clawsqlite.toml` is the single runtime configuration source. Do not create a
second config file, do not rely on shell environment variables for normal
configuration, and do not guess DB paths.

## Bootstrap

Install or upgrade the published package:

```bash
sh bootstrap_deps.sh
```

Then create or edit `./clawsqlite.toml` inside the knowledge instance home. If
no config exists yet:

```bash
clawsqlite knowledge maintenance init-config --out clawsqlite.toml
```

## Validate

After installation and config editing, validate with:

```bash
clawsqlite knowledge maintenance doctor --json
```

Doctor is lightweight by default. Only pass `--check-llm` or
`--check-embedding` when the user explicitly wants provider roundtrip checks.

## Agent Rules

- Use only the official `clawsqlite` CLI.
- Stay in the knowledge instance home when running `clawsqlite knowledge ...`.
- Use the three-level command tree: `record`, `maintenance`, `analysis`.
- Do not call removed flat commands such as `clawsqlite knowledge ingest`.
- Do not vendor, clone, or patch `clawsqlite` inside this skill directory.
- Do not use degraded ingest unless the user explicitly asks for it.
- Report `ERROR`, `ERROR_KIND`, and `NEXT` lines from the CLI directly.

## Common Commands

Strict URL ingest:

```bash
clawsqlite knowledge record ingest \
  --url "https://example.com/article" \
  --category web_article \
  --json
```

Strict text ingest:

```bash
clawsqlite knowledge record ingest \
  --text "A thought worth saving." \
  --title "A saved thought" \
  --category thought \
  --json
```

Search:

```bash
clawsqlite knowledge record search "vector database design" --mode hybrid --topk 5 --json
```

Show one record:

```bash
clawsqlite knowledge record show --id 123 --full --json
```

Maintenance:

```bash
clawsqlite knowledge maintenance reindex --check --json
clawsqlite knowledge maintenance cleanup --days 3 --dry-run --json
clawsqlite knowledge maintenance backup --dry-run --json
```

Analysis:

```bash
clawsqlite knowledge analysis build-interest-clusters --json
clawsqlite knowledge analysis report-interest --days 7 --no-pdf
```

Explicit degraded ingest, only when the user requested it:

```bash
clawsqlite knowledge record ingest \
  --text "Local no-network test." \
  --title "Test" \
  --gen-provider off \
  --allow-heuristic \
  --allow-missing-embedding \
  --json
```
