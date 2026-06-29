---
name: clawsqlite-knowledge
description: Thin Agent adapter for ClawSQLite knowledge-base operations. Use when an OpenClaw/ClawHub Agent needs to ingest a URL, ingest text, search, show a record, or run a conservative status check through `clawsqlite knowledge` while preserving strict ingest and config-first behavior.
---

# ClawSQLite Knowledge Skill

Use this skill as a thin adapter over `clawsqlite knowledge`. Do not treat it as a separate knowledge-base product or a second rule system.

## Non-Negotiable Rules

- Keep core behavior in `clawsqlite knowledge`.
- Use only the project-root `clawsqlite.toml` as the private source of truth for real LLM, embedding, scraper, path, and ingest-policy values.
- Do not guess DB paths, roots, article directories, or working directories.
- Do not accept or interpret API keys in skill input; the CLI reads them from the private `clawsqlite.toml`.
- Do not pass `--allow-heuristic` or `--allow-missing-embedding` unless the user explicitly asks for degraded ingest.
- Do not implement summary generation, tag generation, embedding checks, path resolution, DB access, or search ranking inside the skill.
- Do not expose destructive or broad maintenance actions as ordinary Agent entry points.

## Adapter Script

Call the bundled adapter with a JSON request:

```bash
python skills/clawsqlite-knowledge/scripts/adapter.py --pretty <<'JSON'
{
  "action": "search",
  "query": "sqlite agent knowledge",
  "mode": "hybrid",
  "topk": 5
}
JSON
```

The adapter only supports:

- `ingest_url`
- `ingest_text`
- `search`
- `show`
- `doctor`

## Request Examples

Strict URL ingest:

```json
{
  "action": "ingest_url",
  "url": "https://example.com/article",
  "category": "web"
}
```

Strict text ingest:

```json
{
  "action": "ingest_text",
  "text": "A thought worth saving.",
  "title": "A saved thought",
  "category": "thought"
}
```

Explicit degraded ingest, only when the user requested it:

```json
{
  "action": "ingest_text",
  "text": "Local no-network test.",
  "title": "Test",
  "gen_provider": "off",
  "allow_heuristic": true,
  "allow_missing_embedding": true
}
```

Show a record:

```json
{
  "action": "show",
  "id": 12,
  "full": true
}
```

## Output Contract

Successful calls return:

```json
{
  "ok": true,
  "action": "search",
  "exit_code": 0,
  "data": []
}
```

Failures return:

```json
{
  "ok": false,
  "action": "ingest_text",
  "exit_code": 2,
  "error": {
    "kind": "config_required",
    "message": "ERROR text from clawsqlite",
    "next": "NEXT recovery hint from clawsqlite"
  }
}
```

The adapter structures `ERROR_KIND`, `ERROR`, and `NEXT` lines emitted by the CLI. Prefer reporting those fields directly rather than inventing new recovery advice.
