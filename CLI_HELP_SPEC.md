# CLI Help Spec

This is the compact CLI contract for `clawsqlite knowledge`.

## Namespace

```text
clawsqlite knowledge [common flags] <command> [command flags]
```

Common flags are accepted before or after the subcommand:

- `--tokenizer-ext TOKENIZER_EXT`
- `--vec-ext VEC_EXT`
- `--json`
- `--verbose`

Knowledge commands require `./clawsqlite.toml` in the current component root by default.
They do not search parent directories. `init-config`
is the only command that does not require an existing config.

## Commands

- `init-config`: create a `clawsqlite.toml` template.
- `ingest`: ingest a URL or raw text into the knowledge DB.
- `doctor`: self-check config, DB, schema, vec, embedding, and LLM readiness.
- `search`: search with `fts`, `vec`, or `hybrid`.
- `show`: show one record.
- `export`: export one record to Markdown or JSON.
- `update`: patch fields or regenerate fields for one record.
- `delete`: soft or hard delete one record.
- `reindex`: check/fix/rebuild FTS and vec indexes.
- `rebuild-quality`: regenerate low-quality rows with strict LLM generation.
- `embed-from-summary`: knowledge wrapper around plumbing embedding.
- `maintenance`: prune orphan/backup files.
- `build-interest-clusters`: build interest clusters from existing vectors.
- `inspect-interest-clusters`: inspect cluster quality.
- `report-interest`: generate interest reports.

## Strict Ingest

```text
clawsqlite knowledge ingest (--url URL | --text TEXT) [options]
```

Important options:

- `--gen-provider {openclaw,llm,off}`: override generator provider; default comes from `clawsqlite.toml`.
- `--max-summary-chars N`: override `[ingest].summary_target_chars`.
- `--update-existing`: refresh an existing URL row.
- `--allow-heuristic`: explicitly allow heuristic generation when LLM generation is unavailable.
- `--allow-missing-embedding`: explicitly allow ingest without vector embeddings.

Default config policy is strict:

```toml
[ingest]
require_llm = true
require_embedding = true
fallback = "fail"
```

In strict mode, missing LLM or embedding requirements fail with `ERROR_KIND`
diagnostics instead of silently degrading.

`reindex --fix-missing` follows the same generator policy: default provider
comes from config, and degraded generation requires `--allow-heuristic`.

## Quality Rebuild

```text
clawsqlite knowledge rebuild-quality [--id ID] [--since ISO] [--limit N] [--dry-run] [--json]
```

Selects undeleted rows whose generation metadata is not LLM-quality, regenerates
fields with the configured LLM, rewrites Markdown metadata, refreshes FTS, and
refreshes embeddings when configured.
