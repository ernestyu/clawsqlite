# clawsqlite knowledge Manual

## Description

`clawsqlite knowledge` is the knowledge-base layer for storing articles, notes,
and discussion summaries in SQLite plus Markdown files. It reads
`clawsqlite.toml` on startup. Generic plumbing commands such as
`clawsqlite db`, `clawsqlite index`, `clawsqlite fs`, and `clawsqlite embed`
remain configuration-agnostic.

## Configuration

Knowledge commands find the nearest `clawsqlite.toml` by walking upward from
the current working directory. The directory containing that file is the project
root and the file is the only Knowledge configuration source.

Create a template:

```bash
clawsqlite knowledge init-config --out clawsqlite.toml
```

`[knowledge]` controls root, DB, and Markdown paths. `[ingest]` controls strict
ingest policy and `summary_target_chars`. `[llm]` controls the small LLM,
including context-budget chunking. `[embedding]` controls the embedding endpoint
and vector dimension.

## Commands

`init-config`
: Create a `clawsqlite.toml` template.

`doctor`
: Report active config, DB status, schema health, vec availability, embedding
  readiness, and LLM readiness. Use `--allow-missing-config` only for diagnostics.

`ingest`
: Insert or refresh a URL/text record. Default strict mode fails when configured
  LLM or embedding requirements are not met. Use `--allow-heuristic` and
  `--allow-missing-embedding` only for explicit degraded runs.

`search`
: Search the KB in `hybrid`, `fts`, or `vec` mode. `hybrid` falls back to FTS
  when embeddings are unavailable; `vec` fails fast.

`show`, `export`, `update`, `delete`
: Inspect and maintain individual rows. `update --regen` uses the configured
  generation defaults and needs `--allow-heuristic` for degraded regeneration
  under strict config.

`rebuild-quality`
: Upgrade old heuristic/manual rows by regenerating LLM-quality metadata,
  Markdown headers, FTS rows, and embeddings.

`reindex`, `embed-from-summary`, `maintenance`
: Repair or rebuild indexes and clean Markdown storage. `reindex --fix-missing`
  uses the configured generator and requires `--allow-heuristic` for degraded
  generation under strict config.

`build-interest-clusters`, `inspect-interest-clusters`, `report-interest`
: Optional analysis helpers over existing vectors.

## Error Contract

Important `ERROR_KIND` values:

- `config_required`: no usable `clawsqlite.toml` was found.
- `llm_required`: strict config requires LLM generation.
- `llm_generation_failed`: LLM call or output validation failed.
- `embedding_required`: strict config requires embeddings.
- `fts_tokenizer_fallback`: FTS is usable but CJK recall may be weaker.

Agents should report these errors rather than guessing paths or inventing
metadata.
