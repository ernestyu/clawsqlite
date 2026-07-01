# clawsqlite (knowledge)

**Languages:** English | [中文说明](README_zh.md)

`clawsqlite` is a CLI toolbox for SQLite‑based applications in
[OpenClaw](https://github.com/openclaw/openclaw). The first built‑in
application is a local Markdown + SQLite knowledge base.

This repo currently focuses on the **knowledge** app:

- commands are exposed under `clawsqlite knowledge ...` for users/skills.
- SQLite/FTS/filesystem/embedding maintenance commands are exposed under
  `clawsqlite admin ...` for the current configured knowledge component.

Naming note: `clawsqlite_knowledge` is the Python package that implements the
Knowledge app. `skills/clawsqlite-knowledge` is only a thin OpenClaw/ClawHub
skill instruction directory; it does not contain a second implementation or a
second configuration system.

A local Markdown + SQLite knowledge base for OpenClaw, designed for both
humans and agents.

The knowledge app helps you:

- Ingest URLs or raw text as Markdown files + SQLite records
- Run fast full‑text search over your notes and scraped articles
- Optionally enable vector search via an external embedding service
- Generate titles/summaries/tags with a configured small LLM for reliable ingest
- Keep the KB healthy with explicit maintenance commands (reindex/check/fix + maintenance prune/gc)

> **Status**: already used in real OpenClaw setups. The schema and CLI are kept small and stable on purpose.

---

## 1. Features

- **Pure SQLite backend**
  - `articles` table as source of truth
  - `articles_fts` FTS5 table for full‑text search over title/tags/summary/body
  - `articles_vec` vec0 table for vector search (optional)
- **Markdown storage**
  - Each article is stored as `articles/<id>__<slug>.md`
  - Markdown files include a small METADATA header + MARKDOWN body section
- **Project config**
  - All data lives under a single root directory
  - Knowledge and admin commands read the same component-root `clawsqlite.toml`
  - DB and articles dir default to `<root>/knowledge.sqlite3` and `<root>/articles`
- **Embeddings + LLM**
  - Embeddings: OpenAI‑compatible `/v1/embeddings` API
  - LLM: OpenAI‑compatible `/v1/chat/completions` API
  - Ingest is strict by default when config requires LLM/embedding; degraded ingest needs explicit flags
- **Tag generation & search ranking**
  - LLM ingest produces a whole-article summary, tags, key claims, entities, and content type
  - Heuristic generation is still available for tests or explicit degraded runs via `--allow-heuristic`
  - Search ranking uses tag/query matching as an additional signal on top
    of FTS and vector similarity. Internally, the scorer:
    - embeds both the article summary and the tag string into vec0 tables
      (`articles_vec` and `articles_tag_vec`)
    - **L2-normalizes** both stored vectors and query vectors, then scores
      semantic similarity via **cosine similarity** mapped into [0,1]
      (with a distance->sigmoid fallback for older/partial vec rows)
    - splits the tag channel into **semantic (vector) tag score** and
      **lexical tag match score** using conservative built-in defaults
    - applies an optional **log compression** to the lexical tag score
      so that many partial tag hits don’t overpower the semantic channels
- **CLI first**
  - Simple subcommands: `ingest`, `search`, `show`, `export`, `update`, `delete`, `reindex`, `doctor`
  - Maintenance primitives live under `clawsqlite admin db/index/fs/embed ...`

---

## 2. Requirements

The knowledge app expects an environment similar to the OpenClaw container:

- Python 3.10+ with `sqlite3` and FTS5 enabled
- Python dependencies:
  - `jieba` (optional but strongly recommended for Chinese tag extraction)
  - `pypinyin` (optional; used to generate pinyin slugs for CJK titles)
- sqlite extensions (optional but recommended):
  - `libsimple.so` (tokenizer `simple`) for better CJK tokenization
  - `vec0.so` from [sqlite-vec](https://github.com/asg017/sqlite-vec)
- Network access to your embedding / small LLM HTTP endpoints (if you enable those features)

The repo assumes these paths by default (you can override them):

- Tokenizer extension: `/usr/local/lib/libsimple.so`
- vec0 extension: auto-discovered under `/app/node_modules/**/vec0.so` or system lib dirs

In a fresh environment you typically need to:

- Install `jieba` via pip:

  ```bash
  pip install jieba
  ```

- For `libsimple.so` and `vec0.so`:
  - In the OpenClaw container these are preinstalled.
  - On a custom system you can either:
    - Use distro packages if available (check your Linux distribution), or
    - Build from source following the upstream docs:
      - sqlite-vec: <https://github.com/asg017/sqlite-vec>
      - simple tokenizer: see the OpenClaw docs for building `libsimple.so`.
  - If these extensions are missing, the knowledge app will automatically degrade to:
    - SQLite built‑in tokenizer for FTS
    - If `jieba` is available, optionally pre-segment CJK text in Python for better
      Chinese recall (controlled by `CLAWSQLITE_FTS_JIEBA=auto|on|off`)
    - FTS‑only search when vec0 is unavailable.

For ingest, `clawsqlite.toml` controls whether missing embeddings are fatal.
The default template uses strict ingest (`require_embedding = true`), so a
missing embedding service or vec index fails ingest unless you explicitly pass
`--allow-missing-embedding`.

---

## 3. Installation

### 3.1 From PyPI (recommended for general use)

Once published, the simplest way to use `clawsqlite` is via PyPI:

```bash
pip install clawsqlite

# Then
clawsqlite knowledge --help
```

This installs the `clawsqlite` console script so you can call the CLI from
anywhere in your environment.

### 3.2 From source (development / OpenClaw workspace)

Clone the repo:

```bash
git clone git@github.com:ernestyu/clawsqlite.git
cd clawsqlite
```

(Inside OpenClaw’s workspace this repo may already be present at
`/home/node/.openclaw/workspace/clawsqlite`.)

You can run the knowledge app via the main shell entrypoint:

```bash
# From the repo root
./bin/clawsqlite knowledge --help

# Or explicitly choose a Python binary (e.g. your venv)
CLAWSQLITE_PYTHON=/opt/venv/bin/python ./bin/clawsqlite knowledge --help
```

The recommended CLI entrypoint for skills/users is:

```bash
clawsqlite knowledge ...
```

Use:

```bash
clawsqlite knowledge ...
```

To run a quick self-check of your active config, DB paths, vec0, embedding, and
small LLM readiness, you can use:

```bash
clawsqlite knowledge doctor --json
# or, from source without installing the package
python3 -m clawsqlite_knowledge.cli doctor
```

---

## 4. Configuration

### 4.1 `clawsqlite.toml`

The knowledge app is configured by `clawsqlite.toml`. This is deliberate:
Agents should run the Knowledge CLI and let it load the configured root, DB,
LLM, and embedding settings instead of guessing file names.

Treat `clawsqlite.toml` as the local private source of truth. The checked-in
`clawsqlite.toml.example` is only a public template with placeholders; the real
`clawsqlite.toml` is ignored by git and may contain real API keys.

Config lookup is intentionally single-source: Knowledge commands read only `./clawsqlite.toml` from the current component root.
The current working directory is the component root: the repo root for direct
CLI use, or the skill directory for OpenClaw/ClawHub use. There is no parent
directory search and no config-path override.

Create a template:

```bash
clawsqlite knowledge init-config --out clawsqlite.toml
# or copy the checked-in example
cp clawsqlite.toml.example clawsqlite.toml
```

Minimal shape:

```toml
[knowledge]
root = "./knowledge_data"
db = "knowledge.sqlite3"
articles_dir = "articles"

[ingest]
require_llm = true
require_embedding = true
summary_mode = "llm"
summary_target_chars = 3600
tags_mode = "llm"
tag_count = 8
allowed_categories = [
  "web_article",
  "note",
  "thought",
  "discussion_summary",
  "document",
  "reference",
  "repo",
  "paper",
  "social_post",
]
fallback = "fail"

[llm]
base_url = "https://llm.example.com/v1"
model = "your-small-llm"
api_key = ""  # fill in the real key in your private clawsqlite.toml
context_window_chars = 24000
prompt_reserved_chars = 4000
chunk_overlap_chars = 500

[embedding]
base_url = "https://embed.example.com/v1"
model = "your-embedding-model"
api_key = ""  # fill in the real key in your private clawsqlite.toml
dim = 1024
content = "summary"

[fts]
jieba = "auto"

[search.query]
tag_min = 8
tag_max = 12

[search.weights.mode1] # LLM + Embedding
vec = 0.45
fts = 0.25
tag = 0.15
priority = 0.03
recency = 0.02

[search.weights.mode2] # LLM + no Embedding
fts = 0.60
tag = 0.25
priority = 0.08
recency = 0.07

[search.weights.mode3] # no LLM + Embedding
vec = 0.45
fts = 0.25
tag = 0.15
priority = 0.03
recency = 0.02

[search.weights.mode4] # no LLM + no Embedding
fts = 0.60
tag = 0.25
priority = 0.08
recency = 0.07

[search.tag]
vec_fraction = 0.70
fts_log_alpha = 5.0

[interest]
cluster_algo = "kmeans++"
tag_weight = 0.75
use_pca = true
pca_explained_variance_threshold = 0.95
min_size = 8
max_clusters = 50
kmeans_random_state = 42
kmeans_n_init = 10
kmeans_max_iter = 300
enable_post_merge = true
merge_distance_threshold = 0.06
hierarchical_linkage = "average"
hierarchical_distance_threshold = 0.20
merge_alpha = 0.40

[report]
lang = "en"
```

Relative `root` is resolved relative to the config file. Relative `db` and
`articles_dir` are resolved under `root`. Knowledge root/db/articles paths are
not overridden on the CLI; edit the private `clawsqlite.toml` instead.

### 4.2 Single Configuration Source

`clawsqlite.toml` is the only project configuration file. The Knowledge CLI no
longer auto-loads dot-env files, and this repository intentionally does not ship
a separate environment template. If a setting is part of normal Knowledge
behavior, it should live in the private `clawsqlite.toml` or be passed as an
explicit CLI flag.

Some process environment variables still exist for tests, packaging wrappers,
or low-level native extension discovery. They are runtime hooks, not a second
project configuration file.

### 4.3 Embedding configuration

Embeddings are used for vector search (`articles_vec`) and, by default, for
strict ingest quality. The endpoint, model, dimension, and API key are
configured directly in `[embedding]` inside the private `clawsqlite.toml`.

The knowledge app will:

- Call an OpenAI-compatible `/v1/embeddings` endpoint through `clawsqlite_knowledge.embed.get_embedding()`
- Use `embedding.dim` to define `embedding float[DIM]` in `articles_vec`
- Embed the LLM-generated summary by default (`embedding.content = "summary"`)

If embedding config is incomplete:

- the runtime embedding path is disabled
- strict `ingest` fails when `[ingest].require_embedding = true`
- `ingest --allow-missing-embedding` explicitly permits a no-vector degraded write
- `search` in `mode=hybrid` will auto‑downgrade to FTS‑only and print a `NEXT` hint
  while `mode=vec` fails fast

### 4.4 LLM field generation

In strict ingest, the configured LLM generates structured fields from the whole
article:

- `title`
- `summary`
- `tags`
- `key_claims`
- `entities`
- `category`
- `content_type`

`summary_target_chars` in `clawsqlite.toml` controls the target length for the
summary used later for embeddings. It is intentionally not hard-coded.
If direct text input is already shorter than that target, or the configured
content type is `note`, `thought`, or `discussion_summary`, the stored summary
is the cleaned content itself so short personal notes are not lossy paraphrases.
URL/web-article ingest does not use this short-content passthrough by default.

Long article handling is based on the configured LLM context budget:

```toml
[llm]
context_window_chars = 24000
prompt_reserved_chars = 4000
chunk_overlap_chars = 500
```

If the content fits `context_window_chars - prompt_reserved_chars`, it is sent
in one request. If it does not fit, the generator chunks by that budget,
summarizes chunks, and synthesizes final fields from the chunk summaries.

Heuristic generation still exists, but it is an explicit degraded path:

```bash
clawsqlite knowledge ingest ... --allow-heuristic
```

Without that flag, strict ingest fails if LLM generation is unavailable or the
LLM output does not pass validation.

### 4.5 Search query planning and ranking

Search ranking also uses tags as a small but important signal:

- When `jieba` is available (tags are ordered by importance), we compute a
  continuous tag match score in [0,1] based on how many query keywords
  exactly match the top tags and how early they appear.
- When `jieba` is not available, we fall back to a simple 0/1 bonus for
  any exact tag match, to avoid over‑interpreting a noisy tag order.

For search we build a small query plan:

- `query_refine`: a retrieval-friendly sentence (LLM when enabled; otherwise the raw query)
- `query_tags`: a keyword/phrase list (LLM when enabled; otherwise heuristic v4 extraction)

We use:

- `query_refine` for FTS and for the main semantic channel (summary vectors in `articles_vec`)
- `query_tags` for lexical tag matching and for the tag semantic channel (`articles_tag_vec`)

The final hybrid score is a weighted blend of signals::

    score = w_vec * vec_score + w_fts * fts_score
            + w_tag * tag_score + w_priority * priority_bonus
            + w_recency * recency_bonus

By default the blend roughly means:

- ~45% vector similarity for deep semantic anchoring (summary vectors)
- ~25% BM25 keywords for textual sanity checks (FTS over title/tags/summary)
- ~15% tag channel (split between semantic tag vectors and lexical tag match)
- ~3% priority as a manual pinning mechanism
- ~2% recency to keep new knowledge slightly favored without dominating

For **mixed Chinese/English knowledge bases**, the built-in defaults bias the
ranking toward both semantic search and tags. The approximate shape is:

- summary semantic score for deep recall
- full-text FTS for textual sanity checks
- tag semantic and lexical tag scores for topic anchoring
- small priority/recency bonuses

Search and ranking are Knowledge features. User-facing configuration for these
live in `clawsqlite.toml` under `[search.query]`, `[search.weights.*]`, and
`[search.tag]`.

### 4.6 Scraper configuration

The knowledge app does **not** implement web scraping itself. For `--url`
ingest it runs an external scraper command, configured in `[scraper].cmd`:

Recommended `clawsqlite.toml` usage:

```toml
[scraper]
cmd = "node /path/to/scrape.js --some-flag"
```

The knowledge app will:

- Read this value from `clawsqlite.toml`
- Use `shlex.split()` to build argv (no `shell=True` by default)
- Append the URL as the last argument if you don’t use `{url}`

Scraper output formats:

- **New format (recommended):**

  ```text
  --- METADATA ---
  Title: Some article title
  Author: Someone
  ...

  --- MARKDOWN ---
  # Markdown heading
  Body...
  ```

- **Old format (still supported):**

  ```text
  Title: Some article title
  # Markdown heading
  Body...
  ```

The knowledge app will parse these into `title` and markdown body.

---

## 5. Quickstart

### 5.1 Minimal setup

1. **Clone & cd**

   ```bash
   git clone git@github.com:ernestyu/clawsqlite.git
   cd clawsqlite
   ```

2. **Create and edit `clawsqlite.toml`**

   ```bash
   clawsqlite knowledge init-config --out clawsqlite.toml
   ```

   Fill in `[knowledge].root`, `[llm]`, and `[embedding]` directly in this
   private file, including real API keys.

3. **First strict ingest (text)** – this also creates the DB and basic tables:

   ```bash
   clawsqlite knowledge ingest \
     --text "Hello clawsqlite. This note should be summarized and embedded." \
     --title "First note" \
     --category note \
     --json
   ```

   This will:

   - Create `<root>/knowledge.sqlite3`
   - Create `<root>/articles/000001__first-note.md`
   - Index the record in FTS (and vec if embedding is configured)

   For a no-network test run, make the degraded path explicit:

   ```bash
   clawsqlite knowledge ingest \
     --text "Hello clawsqlite" \
     --title "First note" \
     --category note \
     --gen-provider off \
     --allow-heuristic \
     --allow-missing-embedding \
     --json
   ```

4. **Search it back**:

   ```bash
   clawsqlite knowledge search "Hello" --mode fts --json
   ```

   You should see the record you just created.

### 5.2 Ingest a URL

Assuming you have `[scraper].cmd` set in `clawsqlite.toml`:

```bash
clawsqlite knowledge ingest \
  --url "https://example.com/article" \
  --category web_article \
  --json
```

This will:

- Call your scraper
- Extract title + markdown body
- Generate `summary`/`tags`/`content_type` through the configured LLM in strict mode
- Store everything in DB + markdown

### 5.3 Updating an existing URL (`--update-existing`)

If you know a URL’s content has changed and you want to refresh the existing record:

```bash
clawsqlite knowledge ingest \
  --url "https://example.com/article" \
  --update-existing \
  --json
```

Semantics:

- If a record with this `source_url` exists (including a soft-deleted record),
  and `--update-existing` is set:
  - The knowledge app updates that record’s `title` / `summary` / `tags` / `category` / `priority`
  - Keeps the same `id`
  - Rewrites the markdown file
  - Updates FTS and vec indexes
- If no such record exists, it behaves like a normal ingest.
- If a record exists and `--update-existing` is not set, ingest fails with a
  clear `NEXT` hint instead of relying on a SQLite UNIQUE error.

Note: `source_url` has a UNIQUE index for non‑empty, non‑`Local` values, so each URL maps to at
most one active record.

---

## 6. CLI Overview

All Knowledge commands read `./clawsqlite.toml` from the current component root before resolving
paths. Common flags include `--json` and `--verbose`.
Run `clawsqlite knowledge <command> --help` for full details.

### 6.1 ingest

```bash
clawsqlite knowledge ingest --url URL [options]
clawsqlite knowledge ingest --text TEXT [options]
```

Key options:

- `--url` / `--text`
- `--title`, `--summary`, `--category`, `--priority`
- `--gen-provider {openclaw,llm,off}` (default from `clawsqlite.toml`)
- `--max-summary-chars` (default from `summary_target_chars`)
- `--scrape-cmd` (debug override; normal usage prefers `[scraper].cmd`)
- `--update-existing` (for URL mode)
- `--allow-heuristic`, `--allow-missing-embedding` for explicit degraded ingest

In strict mode, manual title/category are hints only. The stored title, tags,
category, and content type must be generated by the LLM. Tags must match
`[ingest].tag_count` (default `8`), and category/content_type must be identical
and belong to `[ingest].allowed_categories`. Successful JSON output includes
`config_path`, `root`, `db`, `articles_dir`, `generation_quality`,
`embedding_runtime_enabled`, and `embedding_required` so Agents can audit where
the record was written and whether vectors were actually used.

### 6.2 doctor

```bash
clawsqlite knowledge doctor --json
```

By default doctor performs lightweight config/schema checks only. It reports
whether `[llm]` and `[embedding]` fields are complete without making provider
HTTP calls. Use `--check-llm` and/or `--check-embedding` only when you explicitly
want heavier roundtrip checks.

### 6.3 search

```bash
clawsqlite knowledge search "query" --mode hybrid --topk 20 --json
```

Modes:

- `hybrid` – combine vec + FTS
- `fts` – full‑text only
- `vec` – vector only (requires embedding enabled)

Other flags:

- `--candidates` – candidate pool before re‑ranking
- `--llm-keywords {auto,on,off}` – small-LLM usage policy for building `query_refine/query_tags`
- `--gen-provider` – set to `llm` to enable the small LLM configured in `clawsqlite.toml`
- Filters: `--category`, `--tag`, `--since`, `--priority`, `--include-deleted`

### 6.4 show / export / update / delete

- `show` – dump one record (optionally with full markdown content)
- `export` – write a record to a `.md` or `.json` file
- `update` – patch fields or regenerate via generator (id/source_url/created_at are treated as read-only)
- `delete` – soft delete by default (sets `deleted_at`); `--hard` for permanent removal

All read/update/delete style commands **check that the DB file exists** before opening it:

- If `clawsqlite.toml` points to a non‑existent DB path, they report:

  ```text
  ERROR: db not found at /path/to/db. Check current component-root clawsqlite.toml.
  ```

  instead of silently creating an empty DB and then failing with `id not found`.

### 6.5 derived data repair

For one record, use `update --regen`:

```bash
clawsqlite knowledge update --id 12 --regen summary --json
clawsqlite knowledge update --id 12 --regen tags --json
clawsqlite knowledge update --id 12 --regen embedding --json
clawsqlite knowledge update --id 12 --regen all --json
```

For batch repair of missing derived fields/index rows, use:

```bash
clawsqlite knowledge reindex --fix-missing --json
```

`generation_quality` is provenance metadata, not a separate top-level command
surface. Future bulk upgrade policies should be added under explicit repair or
reindex semantics rather than through a standalone quality rebuild command.

### 6.6 reindex

Maintenance operations:

- `reindex --check` – report missing fields/indexes
- `reindex --fix-missing` – regen missing fields/indexes using the configured generator; strict config requires LLM unless `--allow-heuristic` is explicit
- `reindex --rebuild --fts` – rebuild FTS index (via `clawsqlite admin index rebuild`)
- `reindex --rebuild --vec` – clear vec index **only** (no embedding). Refresh
  embeddings through `knowledge update --regen embedding` for one record or
  derived-data repair flows for batches.

The check output includes flags like `vec_available` and
`embedding_runtime_enabled` to help you understand whether vec features are
actually usable for the current DB.

---

### 6.7 admin primitives

Maintenance primitives live under `clawsqlite admin ...`. They are the
administrator surface for the current knowledge component, not a replacement
for the system `sqlite3` tool. By default, admin commands read the same
component-root `clawsqlite.toml` as `clawsqlite knowledge ...` and use its
`[knowledge].db`, `[knowledge].articles_dir`, and runtime service settings.
Flags such as `--db`, `--root`, `--table`, and `--path-col` are explicit
debug/recovery overrides, not the normal path.

- `admin db exec --sql "SELECT ..." --json` prints query rows as JSON; without
  `--json`, inline queries print TSV. Non-query SQL still runs as a script.
- `admin index check` warns instead of tracebacking when `--vec-table` requires
  sqlite-vec but `vec0` is unavailable.
- `admin index rebuild --fts-table ... --fts-cols title,tags,summary` is a
  DB-only/index-only primitive: it rebuilds FTS/vec tables from DB fields and
  does not read, restore, or repair Markdown files. When `--fts-cols` is not
  provided, it rebuilds only FTS columns that are also present in the base DB
  table. File consistency belongs under `admin fs`; application-level rebuild
  workflows should use the `knowledge` wrappers where available.
- `admin fs list-orphans --json` returns counts plus classified `FS_ONLY` /
  `DB_ONLY` paths and a flat `items` array for Agent use.
- `admin fs gc --json --dry-run` previews cleanup as structured data; without
  `--dry-run` it returns deleted FS paths, deleted DB rows, skipped items, and
  summary counts.
- `admin fs repair --json` recreates missing article Markdown files for DB rows:
  URL records try the configured scraper first, while notes/thoughts/discussion
  summaries fall back to DB summary/title content. It does not rebuild indexes
  or delete DB records.
- `admin embed column` wraps embedding provider failures with `ERROR` and `NEXT`
  hints instead of exposing a Python traceback.

Common maintenance workflow:

```bash
# Show the configured articles table schema.
clawsqlite admin db schema --table articles

# Query current DB state.
clawsqlite admin db exec --sql "SELECT COUNT(*) AS n FROM articles;" --json

# Check FTS and vec consistency.
clawsqlite admin index check --table articles --fts-table articles_fts --vec-table articles_vec

# Inspect filesystem/DB mismatches.
clawsqlite admin fs list-orphans --table articles --path-col local_file_path --json

# Recreate DB rows whose Markdown files are missing.
clawsqlite admin fs repair --table articles --path-col local_file_path --json

# Preview cleanup.
clawsqlite admin fs gc --table articles --path-col local_file_path \
  --delete-fs-orphans --delete-db-orphans --dry-run --json

# Apply cleanup.
clawsqlite admin fs gc --table articles --path-col local_file_path \
  --delete-fs-orphans --delete-db-orphans --json
```

---

### 6.8 `report-interest`

```bash
clawsqlite knowledge report-interest --days 7 --lang zh --no-pdf
```

This command is optional analysis/reporting functionality. It lazy-loads
analysis dependencies, so missing `numpy` will not prevent core commands like
`ingest`, `search`, or `show` from starting. Install with
`pip install 'clawsqlite[analysis]'` when you need this report.

### 6.9 interest clustering (topics)

`clawsqlite` can build topic-like clusters from existing `articles_vec` +
`articles_tag_vec`.

#### 6.9.1 Build command

```bash
clawsqlite knowledge build-interest-clusters \
  --algo kmeans++ \
  --use-pca \
  --pca-explained-variance-threshold 0.95 \
  --min-cluster-size 8 \
  --max-clusters 50
```

You can switch backend:

```bash
clawsqlite knowledge build-interest-clusters \
  --algo hierarchical \
  --hierarchical-linkage average \
  --hierarchical-distance-threshold 0.20
```

#### 6.9.2 Vector pipeline (important)

For each eligible article (undeleted, non-empty summary, at least one vec):

1. If both `summary_vec` and `tag_vec` exist:
   - `summary_vec = L2(summary_vec)`
   - `tag_vec = L2(tag_vec)`
   - `mixed = (1-tag_weight)*summary_vec + tag_weight*tag_vec`
   - `interest_vec_1024 = L2(mixed)`
2. If only one branch exists:
   - use that branch after `L2` normalize.

The default tag weight is `0.75` unless explicitly overridden by the
`build-interest-clusters` command.

#### 6.9.3 PCA + clustering backends

- PCA is optional.
- PCA dimension is auto-selected by cumulative explained variance threshold.
- Clustering can run in PCA space, but **persisted centroids are always recomputed in original 1024-d space**.

Backends:

- `kmeans++`
  - standard kmeans++ initialization
  - initial `k0 = min(max_clusters, max(1, floor(n/min_cluster_size)))`
  - supports optional post-merge (`enable_post_merge` + cosine threshold)
- `hierarchical`
  - linkage: `average` or `complete`
  - cut by distance threshold (cosine-distance semantics)
  - falls back to a pure-Python implementation when `scipy` is unavailable
  - no extra post-merge by default

#### 6.9.4 Unified postprocessing and persistence

After initial labels from either backend:

1. Small-cluster reassignment:
   - clusters with size `< min_cluster_size` are reassigned to nearest large
     cluster using original 1024-d vectors.
2. Final centroids:
   - recompute centroid in original 1024-d space, then L2 normalize.
3. Stable cluster IDs:
   - sort by size desc, tie-break by min article_id, renumber to `1..K`.
4. Write DB:
   - `interest_clusters`
   - `interest_cluster_members` (`membership=1.0`)
   - `interest_meta` (build timestamp + algo/pca/tag-weight metadata)

#### 6.9.5 Analysis Knobs

Prefer explicit command flags for one-off analysis runs:

- `--algo`
- `--tag-weight`
- `--use-pca` / `--no-pca`
- `--pca-explained-variance-threshold`
- `--min-size`
- `--max-clusters`
- `--kmeans-random-state`
- `--kmeans-n-init`
- `--kmeans-max-iter`
- `--enable-post-merge` / `--disable-post-merge`
- `--merge-distance-threshold`
- `--hierarchical-linkage`
- `--hierarchical-distance-threshold`

Interest clustering and reports are part of `clawsqlite knowledge`. Durable
user-facing defaults should live in `clawsqlite.toml`, not in a separate ENV
file.

#### 6.9.6 Cluster quality inspection

为了调参和观察兴趣簇结构，可使用内置分析命令：

```bash
clawsqlite knowledge inspect-interest-clusters \
  --vec-dim 1024
```

提示：该命令依赖 `numpy`；如需生成 PNG 图，还需要 `matplotlib`。

- 仅统计：`pip install 'clawsqlite[analysis]'`
- 统计 + 绘图：`pip install 'clawsqlite[analysis,plot]'`

该命令会：

1. 从 `interest_clusters` / `interest_cluster_members` 读出当前簇；
2. 以与构建簇相同的方式重建 interest 向量；
3. 打印每个簇的：`size / n_members / mean_radius / max_radius`；
4. 打印簇心之间 `1 - cos` 距离的 `min / max / median`；
5. 若环境可用 `matplotlib`，生成一张 PCA 2D 散点图：
   - 点位置：簇心的 PCA 降维坐标 (PC1, PC2)；
   - 点大小：与簇大小成比例（使用开根号压缩动态范围）；
   - 点颜色：对应该簇的 `mean_radius`，配色为 `viridis`，色条标签
     为 `mean_radius (1 - cos)`；
   - PNG 默认写到当前工作目录：`./interest_clusters_pca.png`。

可以加 `--no-plot` 仅打印数值统计，不生成图片：

```bash
clawsqlite knowledge inspect-interest-clusters \
  --vec-dim 1024 \
  --no-plot
```

实际调参建议：

1. 先用当前 KB 跑一轮 `build-interest-clusters`，再运行
   `inspect-interest-clusters` 看各簇的大小、半径以及簇心距离；
2. 使用辅助脚本（`tests/test_interest_merge_suggest.py`）或类似逻辑，估算一个
   合适的 merge distance；
3. 通过显式 CLI 参数重新构建，并用 `inspect-interest-clusters` 观察结构变化。

---

### 6.10 maintenance

```bash
clawsqlite knowledge maintenance prune --days 3 --dry-run
```

This scans for orphaned files, old `.bak_*` backups, and broken DB paths.
Use `--dry-run` to preview deletions; `maintenance gc` is an alias of `prune`.

---

## 7. Notes on File Naming & Titles

Markdown files are named:

```text
<id:06d>__<slugified-title>.md
```

- `id` comes from the `articles` table
- `slugified-title` is derived from the title:
  - If `pypinyin` is available, CJK tokens are converted to pinyin;
  - ASCII letters/digits are preserved; other symbols become `-`;
  - Repeated `-` are collapsed; empty results fall back to `untitled`.

For CJK titles, the filename is typically a pinyin‑based slug. If `pypinyin`
is not installed, it may fall back to `untitled`. This does not affect functionality:

- The real title is stored in the DB
- Search operates on DB fields and FTS, not filenames

We may refine this strategy in future (e.g. supporting CJK slugs or ID‑only filenames), but the
current format is stable and works well with existing tools.

---

## 8. ClawHub Skill Wrapper

This repo includes thin ClawHub/OpenClaw skill instructions under
[skills/clawsqlite-knowledge](skills/clawsqlite-knowledge/SKILL.md).

Do not confuse the two similarly named directories:

- `clawsqlite_knowledge/` is the Python implementation of the Knowledge app.
- `skills/clawsqlite-knowledge/` is a thin Agent-facing instruction wrapper.

The skill does not ship a runtime JSON wrapper. Agents should `cd` to the skill
directory, treat that directory as the component root, and run
`clawsqlite knowledge ...` directly. Core logic, strict ingest, config loading,
and error semantics remain owned by `clawsqlite knowledge`.

## 9. Chinese Documentation

中文完整说明见 [README_zh.md](README_zh.md)。

---

如果你在使用过程中遇到问题或有改进建议，欢迎在仓库里开 issue / PR。

---

## License

MIT © Ernest Yu
