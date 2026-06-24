# Agent Usage Contract

This file is for AI Agents using `clawsqlite knowledge ...`.

## First Rule

Always let the Knowledge CLI load its configuration before touching the DB.
Do not guess database names or scan random directories.

Config lookup order:

1. `--config /path/to/clawsqlite.toml`
2. `$CLAWSQLITE_CONFIG`
3. nearest `clawsqlite.toml` found by walking upward from the current working directory

If config is missing, stop and report the `ERROR_KIND: config_required` message.

## Knowledge vs Plumbing

- `clawsqlite knowledge ...` is the knowledge-base application. It reads `clawsqlite.toml`.
- `clawsqlite db ...`, `clawsqlite index ...`, `clawsqlite fs ...`, and `clawsqlite embed ...` are generic plumbing commands. They do not read `clawsqlite.toml`.

Use Knowledge commands for article/note ingest, search, show, update, delete, reindex, and quality rebuilds. Use Plumbing only when you intentionally need generic SQLite operations.

## Strict Ingest

Default Knowledge ingest is strict when `clawsqlite.toml` says:

```toml
[ingest]
require_llm = true
require_embedding = true
fallback = "fail"
```

In strict mode:

- missing or failing LLM field generation fails the command;
- missing embedding configuration or vec sync fails the command;
- the Agent must not invent tags or silently use heuristic tags.

Explicit degraded ingest is allowed only when the command includes the relevant flag:

```bash
clawsqlite knowledge ingest ... --allow-heuristic
clawsqlite knowledge ingest ... --allow-missing-embedding
```

Use these flags only when the user explicitly asks for degraded ingest or a test needs a no-network path.

## Summary For Embedding

The LLM-generated `summary` is the default content used for embeddings.
`summary_target_chars` is configured in `clawsqlite.toml`; do not assume a hard-coded value.

For long articles, the generator uses:

- `llm.context_window_chars`
- `llm.prompt_reserved_chars`
- `llm.chunk_overlap_chars`

It sends the full article in one request only when the content fits the configured budget. Otherwise it chunks first, summarizes chunks, then synthesizes final fields.

## Useful Commands

Create a config template:

```bash
clawsqlite knowledge init-config --out clawsqlite.toml
```

Check active config and DB status:

```bash
clawsqlite knowledge doctor --json
```

Strict ingest:

```bash
clawsqlite knowledge ingest --url "https://example.com/article" --category web --json
```

Rebuild old low-quality rows with LLM fields:

```bash
clawsqlite knowledge rebuild-quality --json
```
