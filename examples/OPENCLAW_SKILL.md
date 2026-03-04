---
name: clawkb
description: OpenClaw-facing skill for the Clawkb knowledge base. Provides URL ingest, full-text / vector search, and basic maintenance via the ./bin/clawkb entrypoint.
---

# Skill: Clawkb (local Markdown + SQLite knowledge base)

This skill describes how an OpenClaw agent should interact with a Clawkb
instance that lives on the same machine.

> **Assumptions:**
> - The Clawkb repo has been cloned (e.g. under `/home/node/.openclaw/workspace/Clawkb`).
> - A project-level `.env` exists in the repo root with embedding / vec /
>   scraper configuration.
> - The agent can run shell commands on the host.

## 1. Critical paths

- **Project root**: `<PATH_TO_CLAWKB_REPO>`
- **Data root (default)**: `<PATH_TO_CLAWKB_REPO>/clawkb_data`
- **DB path (default)**: `<PATH_TO_CLAWKB_REPO>/clawkb_data/clawkb.sqlite3`
- **Articles dir (default)**: `<PATH_TO_CLAWKB_REPO>/clawkb_data/articles/`

Most of these defaults can be overridden via `.env` (`CLAWKB_ROOT`,
`CLAWKB_DB`, `CLAWKB_ARTICLES_DIR`), but an agent should treat them as
implementation details and always go through `./bin/clawkb`.

## 2. Environment requirements

The only environment variable an agent should set inline is the Python
interpreter to use for Clawkb (if the system default is not correct):

```bash
export CLAWKB_PYTHON=/opt/venv/bin/python
```

All other configuration (embedding endpoints, vec extension path, scraper
command, root override) should live in the project `.env` and be managed by
humans / ops, not by the agent.

## 3. Main entrypoint

All operations MUST go through the shell entrypoint:

```bash
cd <PATH_TO_CLAWKB_REPO>
./bin/clawkb <subcommand> [args...]
```

Examples below assume the repo lives at:

```text
/home/node/.openclaw/workspace/Clawkb
```

## 4. Protocols

### 4.1 Ingest a web page or article

Ingest by URL (scraper and embedding config are taken from `.env`):

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb ingest \
    --url "https://example.com/article" \
    --category "web" \
    --json
```

Notes:

- If the URL already exists and the agent wants to refresh the content, it
  should add `--update-existing`.
- `--category` can be used to tag different sources (e.g. `"微信公众号"`, `"github"`).

### 4.2 Hybrid search (FTS + vectors)

Search for relevant articles using the default hybrid mode:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb search "<QUERY_TEXT>" --json
```

The agent should parse the JSON output to retrieve:

- `id` – article id (for use with `show` / `export` / `update` / `delete`)
- `score` – relevance score
- `title`, `summary`, `category`, `tags`

If vector search is disabled (no embedding config), Clawkb will
automatically fall back to FTS-only search.

### 4.3 Show a record

To inspect a single record:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb show --id <ID> --full
```

- `--full` includes the Markdown content in the output.
- Without `--full`, only metadata is printed.

### 4.4 Update fields or regenerate derived data

Patch title/summary/tags:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb update \
    --id <ID> \
    --title "New Title" \
    --summary "New long summary" \
    --tags "tag1,tag2" \
    --json
```

Regenerate summary/tags using the configured provider:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb update \
    --id <ID> \
    --regen summary \
    --gen-provider openclaw \
    --json
```

Regenerate embedding only:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb update \
    --id <ID> \
    --regen embedding \
    --json
```

### 4.5 Delete a record

Soft delete (mark as deleted, keep data for maintenance):

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb delete --id <ID>
```

Agents should prefer soft delete; physical cleanup can be done by humans
or scheduled maintenance commands.

### 4.6 Maintenance and status

Basic index maintenance:

```bash
cd /home/node/.openclaw/workspace/Clawkb
CLAWKB_PYTHON=/opt/venv/bin/python \
  ./bin/clawkb reindex --check --fix
```

> Note: A dedicated `status` subcommand may be added in future versions of
> Clawkb to summarise DB health and coverage. For now, agents can infer
> health from the success/failure of `reindex --check` and basic `search`
> calls.

## 5. Sovereignty rules (for agents)

1. **Single entrypoint**: Do not call `python -m clawkb` directly; always use
   `./bin/clawkb` from the repo root.
2. **Zero ad-hoc exports**: Only set `CLAWKB_PYTHON` when needed. All other
   environment configuration must come from `.env`.
3. **No direct DB writes**: Do not manipulate the SQLite files directly;
   always go through the CLI.

With this protocol, a new agent can obtain full operational control over a
Clawkb instance by learning a small, stable set of shell commands, without
needing to know the internal schema or Python package layout.
