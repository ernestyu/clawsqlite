---
name: clawsqlite-knowledge
description: Thin OpenClaw/ClawHub wrapper instructions for using `clawsqlite knowledge` from a fixed component root.
---

# ClawSQLite Knowledge Skill

This skill is a thin wrapper around `clawsqlite knowledge`. It is not a second
knowledge-base product, not a runtime wrapper script, and not a separate rules
engine.

Naming note: `clawsqlite_knowledge/` in the repository is the Python package
that implements the Knowledge app. `skills/clawsqlite-knowledge/` is only this
thin Agent-facing instruction layer.

## Component Root

The skill directory is the component root. Before running any Knowledge command,
the Agent must `cd` to this directory:

```bash
cd <workspace>/skills/clawsqlite-knowledge
```

The only Knowledge configuration file is:

```text
./clawsqlite.toml
```

`clawsqlite knowledge` reads only `./clawsqlite.toml` from the current component
root. It does not search parent directories, read a config-path environment
variable, or accept a config-path override.

## Bootstrap

First-time setup:

```bash
sh bootstrap.sh
```

Then edit `clawsqlite.toml` directly. It is the private source of truth for:

- `[knowledge]` root, DB, and article paths
- `[llm]` endpoint, model, API key, and context budget
- `[embedding]` endpoint, model, API key, dimension, and content policy
- `[scraper]` URL ingest command
- `[ingest]` strict policy, summary target length, tag count, and allowed categories

Do not put real Knowledge runtime configuration in shell environment variables.

## Agent Rules

- Stay in the component root when running `clawsqlite knowledge`.
- Do not guess DB paths, roots, article directories, or working directories.
- Do not edit a second configuration file elsewhere.
- Do not pass path overrides for root, DB, articles, tokenizer, or vec extension
  as normal Agent workflow.
- Do not use degraded ingest unless the user explicitly asks for it.
- Report `ERROR_KIND`, `ERROR`, and `NEXT` lines from the CLI directly.

## Common Commands

Status check:

```bash
clawsqlite knowledge maintenance doctor --json
```

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

`--title` and `--category` are hints during strict LLM ingest. The stored title,
tags, category, and content type must come from the configured LLM; manual tag
input is intentionally not part of the ingest action surface.
Successful JSON output includes `config_path`, `root`, `db`, `articles_dir`,
`generation_quality`, `embedding_runtime_enabled`, and `embedding_required`;
check these fields before telling the user where data was written.

Doctor is lightweight by default:

```bash
clawsqlite knowledge maintenance doctor --json
```

Only pass `--check-llm` or `--check-embedding` when the user explicitly wants a
provider roundtrip check.

Search:

```bash
clawsqlite knowledge record search "sqlite agent knowledge" --mode hybrid --topk 5 --json
```

Show one record:

```bash
clawsqlite knowledge record show --id 12 --full --json
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
