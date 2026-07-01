# CLI Help Spec

This is the compact CLI contract for `clawsqlite knowledge`.

## Namespace

```text
clawsqlite knowledge [common flags] <group> <command> [command flags]
```

Groups:

- `record`: knowledge record operations.
- `maintenance`: health checks, reindexing, cleanup, and backup.
- `analysis`: interest clustering and reports.

Legacy flat commands such as `clawsqlite knowledge ingest` and
`clawsqlite knowledge doctor` are not supported.

## Record Commands

- `record ingest`: ingest a URL or raw text.
- `record search`: search with `fts`, `vec`, or `hybrid`.
- `record show`: show one record.
- `record export`: export one record to Markdown or JSON.
- `record update`: patch fields or regenerate derived fields for one record.
- `record delete`: soft or hard delete one record.

## Maintenance Commands

- `maintenance init-config`: create a `clawsqlite.toml` template.
- `maintenance doctor`: self-check config, DB, schema, vec, embedding, and LLM readiness.
- `maintenance reindex`: check/fix/rebuild FTS and vec indexes.
- `maintenance cleanup`: clean orphan/backup files and report broken paths.
- `maintenance backup`: create one DB + `articles/` archive and upload it to configured S3.

`maintenance backup` reads `[backup]` and `[backup.s3]` from
`clawsqlite.toml`. It supports `--dry-run` but does not expose a local `--out`
primary path.

## Analysis Commands

- `analysis build-interest-clusters`: build interest clusters from existing vectors.
- `analysis inspect-interest-clusters`: inspect cluster quality.
- `analysis report-interest`: generate interest reports.

## Strict Ingest

```text
clawsqlite knowledge record ingest (--url URL | --text TEXT) [options]
```

Important options:

- `--gen-provider {openclaw,llm,off}`: override generator provider; default comes from `clawsqlite.toml`.
- `--max-summary-chars N`: override `[ingest].summary_target_chars`.
- `--update-existing`: refresh an existing URL row.
- `--allow-heuristic`: explicitly allow heuristic generation when LLM generation is unavailable.
- `--allow-missing-embedding`: explicitly allow ingest without vector embeddings.

Default config policy is strict. Missing LLM or embedding requirements fail
with `ERROR_KIND` diagnostics instead of silently degrading.
