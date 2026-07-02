# clawsqlite knowledge Manual

## Description

`clawsqlite knowledge` is the knowledge-base layer for storing articles, notes,
and discussion summaries in SQLite plus Markdown files. It reads
`./clawsqlite.toml` from the current knowledge instance home before running
commands.

The command surface is grouped into three required groups:

- `record` for knowledge records.
- `maintenance` for health checks, repair, cleanup, and configured S3 backup.
- `analysis` for clustering and reports.

Legacy flat commands are intentionally unsupported. Use
`clawsqlite knowledge record ingest`, not `clawsqlite knowledge ingest`.

## Configuration

Knowledge commands read only `./clawsqlite.toml` from the current knowledge
instance home. This file is the local private source of truth, and the same
directory should contain the configured DB and `articles/` directory.

Create a template:

```bash
clawsqlite knowledge maintenance init-config --instance default
cd ~/.local/share/clawsqlite-knowledge/default
```

Use `--home /path/to/knowledge-home` for an explicit custom instance home.
`init-config` refuses source repositories and `skills/` directories as
instance homes.

`[knowledge]` keeps `root = "."` and controls DB and Markdown paths relative to
the instance home. `[ingest]`, `[llm]`,
`[embedding]`, `[search]`, `[interest]`, and `[report]` control the knowledge
runtime. `[backup]` / `[backup.s3]` controls remote S3 backup.

## Commands

`record ingest`
: Insert or refresh a URL/text record. Default strict mode fails when configured
  LLM or embedding requirements are not met.

`record search`
: Search the KB in `hybrid`, `fts`, or `vec` mode.

`record show`, `record export`, `record update`, `record delete`
: Inspect and maintain individual records.

`maintenance doctor`
: Report active config, DB status, schema health, vec availability, embedding
  readiness, LLM readiness, and scraper configuration. Use `--check-scraper`
  only when an explicit scraper runtime roundtrip is desired. The JSON report
  includes `url_ingest_ready` with missing/failed/not_checked prerequisites.

`maintenance reindex`
: Check, fix, or rebuild derived DB indexes.

`maintenance cleanup`
: Clean orphan files, old backup files, and broken DB paths.

`maintenance backup`
: Package the configured DB and `articles/` directory into one archive and
  upload it to the S3/S3-compatible target defined in `clawsqlite.toml`.
  `--dry-run` validates and packages without uploading.

`analysis build-interest-clusters`, `analysis inspect-interest-clusters`, `analysis report-interest`
: Optional analysis helpers over existing vectors.

## Error Contract

Important `ERROR_KIND` values:

- `config_required`: no usable `clawsqlite.toml` was found.
- `legacy_flat_command`: a removed flat command was used.
- `llm_required`: strict config requires LLM generation.
- `llm_generation_failed`: LLM call or output validation failed.
- `embedding_required`: strict config requires embeddings.
- `backup_config_required`: S3 backup configuration is incomplete.

Agents should report these errors rather than guessing paths, inventing
metadata, or retrying removed command paths.
