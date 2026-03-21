# clawsqlite Plumbing Layer Design

This document describes the proposed **"plumbing" layer** for the
`clawsqlite` CLI – low-level, generic commands around SQLite, full-text
/ vector indexes, and the associated filesystem layout.

These commands are intended to be:

- **Generic** – usable by multiple applications (KB, reading, future apps);
- **Predictable** – do one thing, with clear input/output;
- **Composable** – higher-level "porcelain" commands (e.g. `clawsqlite kb`
  or `clawsqlite reading`) should be able to call them internally.

The CLI layout is:

```bash
clawsqlite db ...     # raw SQLite operations (schema/exec/backup/vacuum)
clawsqlite index ...  # generic FTS / vector index operations
clawsqlite fs ...     # filesystem + DB consistency helpers
```

Below, each command is described with:

- **What it does**
- **When to use it** (typical scenarios)
- **CLI signature**
- **Concrete examples**

---

## 1. `clawsqlite db` – raw SQLite operations

These commands work on a single SQLite database file and do **not** assume
any specific schema (no `articles`, no `category`, etc.). They are generic
maintenance / inspection tools.

### 1.1 `clawsqlite db schema`

**What it does**

Prints the schema (tables, indexes, views) of a SQLite database, optionally
filtered to a single table.

**When to use it**

- You want to see **what tables/columns exist** in a DB (for debugging or
  documentation).
- You are writing a new app on top of an existing DB and need to understand
  its layout.

**Signature**

```bash
clawsqlite db schema --db PATH [--table NAME]
```

- `--db PATH` – path to the `.db` file
- `--table NAME` – optional; if provided, only show schema for that table

**Examples**

```bash
# Show full schema
clawsqlite db schema --db ~/.clawsqlite/knowledge.db

# Show only the schema for table 'articles'
clawsqlite db schema --db ~/.clawsqlite/knowledge.db --table articles
```

---

### 1.2 `clawsqlite db exec`

**What it does**

Executes arbitrary SQL against a SQLite database. This is a **low-level
escape hatch** – powerful but potentially dangerous if misused.

**When to use it**

- One-off data migrations (e.g. add a column, backfill some values).
- Manual fixes during development (e.g. reset a flag, delete test rows).
- Scripting: call from shell scripts when you need to run SQL and don't
  want to invoke the sqlite3 CLI directly.

**Signature**

```bash
clawsqlite db exec --db PATH (--sql SQL | --file SQL_FILE)
```

- `--db PATH` – path to the `.db` file
- `--sql SQL` – inline SQL string
- `--file SQL_FILE` – path to a `.sql` file containing statements

**Examples**

```bash
# Mark all low-priority articles as archived
clawsqlite db exec --db ~/.clawsqlite/knowledge.db \
  --sql "UPDATE articles SET archived = 1 WHERE priority <= 0;"

# Run a migration script
clawsqlite db exec --db ~/.clawsqlite/knowledge.db --file migrations/001_add_flags.sql
```

> NOTE: This is plumbing. Application-level commands (e.g. `kb migrate`)
> should wrap specific migrations instead of asking users to type raw SQL.

---

### 1.3 `clawsqlite db vacuum`

**What it does**

Runs `VACUUM` on the database: compacts the file, reclaims free space,
rebuilds the B-tree structures.

**When to use it**

- After deleting many rows (KB cleanup, reading history cleanup, etc.).
- Periodic maintenance to keep the DB small and efficient.

**Signature**

```bash
clawsqlite db vacuum --db PATH
```

**Examples**

```bash
# Compact the knowledge DB after heavy cleanup
clawsqlite db vacuum --db ~/.clawsqlite/knowledge.db
```

---

### 1.4 `clawsqlite db analyze`

**What it does**

Runs `ANALYZE` on the database: updates SQLite's **internal statistics**
for each index and table (e.g. in `sqlite_stat1`). These statistics help the
SQLite **query planner** choose which index to use for a given query.

Concretely, SQLite learns things like:

- roughly how many rows each index covers;
- how selective certain columns are ("if I filter on this column, will I
  narrow down to a few rows or almost the whole table?").

It does **not** change your data or schema – only SQLite's internal "mental
model" of the data distribution.

**When to use it**

- After large bulk imports (e.g. ingesting many articles / books).
- After deleting or updating a large portion of the table.
- After creating or dropping indexes.

On small DBs, SQLite will usually do fine without frequent `ANALYZE`, but
running it after big data changes can improve performance of complex
queries.

**Signature**

```bash
clawsqlite db analyze --db PATH
```

**Examples**

```bash
# After a large ingest or heavy cleanup
clawsqlite db analyze --db ~/.clawsqlite/knowledge.db
```

---

### 1.5 `clawsqlite db backup`

**What it does**

Creates a copy of a SQLite database to a specified path. Optionally adds a
timestamp.

**When to use it**

- Before risky operations (schema changes, bulk deletes).
- For periodic backups (e.g. cron job).

**Signature**

```bash
clawsqlite db backup --db PATH --out PATH [--add-timestamp]
```

- `--db PATH` – source DB
- `--out PATH` – destination path (file or directory)
- `--add-timestamp` – if set and `--out` is a directory, append a timestamped
  filename

**Examples**

```bash
# Backup to explicit path
clawsqlite db backup --db ~/.clawsqlite/knowledge.db --out backups/knowledge.db

# Backup with timestamped filename
clawsqlite db backup --db ~/.clawsqlite/knowledge.db --out backups --add-timestamp
# e.g. creates backups/knowledge-2026-03-21T06-30-00.db
```

---

## 2. `clawsqlite index` – FTS / vector index operations

These commands assume you have a **base table** (e.g. `articles`) and one or
more **index tables** for it:

- a full-text search (FTS) virtual table (e.g. `articles_fts`), and/or
- a vector index table/virtual table (e.g. `articles_vec` storing embeddings).

They do **not** assume KB-specific columns like `category` or `priority` –
only the relationship "this table has these associated indexes".

### 2.1 `clawsqlite index check`

**What it does**

Checks index consistency for a given table:

- verifies that FTS rows match base table rows;
- optionally verifies that vector index rows match base table rows.

**When to use it**

- Suspect index corruption or mismatch (e.g. after manual SQL updates).
- As part of a periodic health check.

**Signature**

```bash
clawsqlite index check \
  --db PATH \
  --table NAME \
  [--fts-col NAME] \
  [--vec-col NAME]
```

- `--db PATH` – database file
- `--table NAME` – base table name (e.g. `articles`)
- `--fts-col NAME` – optional, FTS index column name
- `--vec-col NAME` – optional, vector index column name

**Examples**

```bash
# Check FTS index for articles.content_fts
clawsqlite index check \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --fts-col content_fts

# Check both FTS and vector index
clawsqlite index check \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --fts-col content_fts \
  --vec-col embedding
```

Output could be a short report:

```text
[OK] FTS index matches base table (1234 rows)
[OK] Vector index matches base table (1234 rows)
```

or detailed warnings if mismatches are found.

---

### 2.2 `clawsqlite index rebuild`

**What it does**

Rebuilds one or both indexes (FTS / vector) for a table based on the
current base data.

**When to use it**

- After significant manual changes to base table rows.
- When `index check` reports mismatch.
- After upgrading the indexing scheme.

**Signature**

```bash
clawsqlite index rebuild \
  --db PATH \
  --table NAME \
  [--fts-col NAME] \
  [--vec-col NAME]
```

**Examples**

```bash
# Rebuild FTS index
clawsqlite index rebuild \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --fts-col content_fts

# Rebuild both FTS and vector index
clawsqlite index rebuild \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --fts-col content_fts \
  --vec-col embedding
```

> Application-level commands like `kb reindex` would typically wrap this
> with fixed `--table` / `--fts-col` / `--vec-col` arguments.

---

### 2.3 `clawsqlite index search` (optional core)

**What it does**

Provides a **generic search primitive** on top of FTS and/or vector indexes.
It is intentionally low-level and only cares about:

- "给我一个查询字符串"
- "告诉我 FTS / 向量索引用的是哪一套"
- "我给你按分数排好的一组 rowid + 分数"

不关心 `title/category/priority` 等业务字段。

内部可以支持三种模式（具体策略由实现决定）：

1. **FTS-only** – 只用 FTS5 做 BM25 排序；
2. **Vec-only** – 只用向量相似度排序；
3. **Hybrid** – FTS + 向量分数做一个混合分数。

上层（`kb search` / `reading search`）决定：

- 这次要用哪种模式；
- 怎么解释这些 rowid（从哪张表 SELECT 出标题/摘要等）。

**Signature**

```bash
clawsqlite index search \
  --db PATH \
  --table NAME \
  [--fts-col NAME] \
  [--vec-col NAME] \
  --query STRING \
  [--limit N]
```

**Examples**

```bash
# Hybrid search on articles
clawsqlite index search \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --fts-col content_fts \
  --vec-col embedding \
  --query "LLM quantization" \
  --limit 20
```

Output (for plumbing) could be JSON lines (one per match):

```json
{"rowid": 123, "fts_score": 1.23, "vec_score": 0.87, "hybrid_score": 0.95}
{"rowid": 45,  "fts_score": 0.98, "vec_score": 0.90, "hybrid_score": 0.94}
...
```

Application-level search commands would then:

- read these row IDs;
- join with the base table to fetch titles, categories, etc.;
- format user-facing output（比如按 hybrid_score 排序后打印 title/summary）。

---

## 3. `clawsqlite fs` – filesystem + DB helpers

Many SQLite-backed apps pair a DB with a set of files on disk – for
example:

- a knowledge base (SQLite + Markdown 文章目录),
- 一个阅读库（SQLite + 拆开的章节文件 / 资源），
- 或者其它 "SQLite + 文件" 模式的应用。

这类命令是为这种模式设计的：**同时看磁盘和 DB，把两边的状态对齐。**
它们不编码 KB 特有的字段（如 `category`），只假设：

- 有一个根目录 `--root` 放内容文件；
- DB 某个表有一列是“文件路径”（例如 `articles.relpath`）。

纯表内应用（所有内容都在 SQLite 表里，不写 Markdown / 附件）可以完全
不使用 `clawsqlite fs` 这一组命令。

### 3.1 `clawsqlite fs list-orphans`

**What it does**

Lists mismatches between the DB and filesystem:

- files on disk with no matching DB row;
- DB rows pointing to missing files.

**When to use it**

- Before cleanup, to see what would be affected.
- During debugging when something "disappears" from the app but not from
  disk (or vice versa).

**Signature**

```bash
clawsqlite fs list-orphans \
  --root DIR \
  --db PATH \
  --table NAME \
  --path-col NAME
```

- `--root DIR` – root directory for content files
- `--db PATH` – database path
- `--table NAME` – table that stores file references
- `--path-col NAME` – column in that table that stores relative paths

**Examples**

```bash
# List orphans for KB articles
clawsqlite fs list-orphans \
  --root ~/kb/articles \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --path-col relpath
```

Output could be a simple report:

```text
[FS_ONLY] articles/old-note-1.md
[DB_ONLY] rowid=42 path="articles/missing-note.md"
```

---

### 3.2 `clawsqlite fs gc`

**What it does**

Performs a **garbage-collection** pass over the filesystem + DB:

- optionally deletes files that are not referenced in the DB;
- optionally removes DB rows whose files are missing;
- can be run in dry-run mode first.

**When to use it**

- Periodic cleanup of a KB / reading library to remove stale files.
- After manual file operations (moving/deleting files outside the tool).

**Signature**

```bash
clawsqlite fs gc \
  --root DIR \
  --db PATH \
  --table NAME \
  --path-col NAME \
  [--delete-fs-orphans] \
  [--delete-db-orphans] \
  [--dry-run]
```

**Examples**

```bash
# Dry-run: see what would be cleaned up
clawsqlite fs gc \
  --root ~/kb/articles \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --path-col relpath \
  --delete-fs-orphans \
  --delete-db-orphans \
  --dry-run

# Actual cleanup
clawsqlite fs gc \
  --root ~/kb/articles \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --path-col relpath \
  --delete-fs-orphans \
  --delete-db-orphans
```

Application-level commands like `kb maintenance` could wrap this with
predefined `--root` / `--table` / `--path-col` values.

---

### 3.3 `clawsqlite fs reconcile` (optional)

**What it does**

Attempts to reconcile mismatches between DB and filesystem:

- for DB rows pointing to missing files, optionally create empty stub
  files so editors can recreate content;
- for files without DB rows, optionally register them in a special import
  table or queue.

**When to use it**

- When you want to restore invariant "every DB row has a file" without
  deleting the DB rows.
- When you have manually dropped Markdown files into the content folder and
  want to import them.

**Signature**

```bash
clawsqlite fs reconcile \
  --root DIR \
  --db PATH \
  --table NAME \
  --path-col NAME \
  [--create-missing-files] \
  [--register-orphan-files]
```

**Examples**

```bash
# Create empty files for DB rows whose content files are missing
clawsqlite fs reconcile \
  --root ~/kb/articles \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --path-col relpath \
  --create-missing-files

# Register orphan files into an 'import_queue' table (hypothetical)
clawsqlite fs reconcile \
  --root ~/kb/articles \
  --db ~/.clawsqlite/knowledge.db \
  --table articles \
  --path-col relpath \
  --register-orphan-files
```

Concrete behavior (e.g. which table to use for import) would be fleshed out
once KB/reading use-cases are clearer, but the CLI surface stays generic.

---

## 4. How applications would use this

- **Knowledge app (`clawsqlite knowledge ...`)**:
  - `knowledge reindex` → wraps `index check` + `index rebuild` with fixed table/cols;
  - `knowledge maintenance` → wraps `fs gc` + `db vacuum` + `index check`;
  - `knowledge search` → internally calls `index search` then joins `rowid` to
    `articles` table.

- **Reading app (`clawsqlite reading ...`)**:
  - `reading ingest-epub` → writes rows to a `reading_items` table, then
    calls `index rebuild` for that table;
  - `reading maintenance` → wraps `fs gc` for the reading root.

This way, **all the knowledge/reading-specific verbs** stay under their own
namespaces (`knowledge`, `reading`), while the low-level DB/FS/index
machinery is consolidated and reusable.

---

## 5. Refactor roadmap (implementation plan)

This section is an explicit checklist for refactoring the existing
`clawkb` CLI into the new `clawsqlite` structure.

### 5.1 Step 0 – Inventory current commands

Goal: get a clear map of what exists today.

Actions:

1. In `clawkb/cli.py`, list all current commands in a table, e.g.:

   | Command                      | Description                              |
   |------------------------------|------------------------------------------|
   | `clawsqlite knowledge ingest`| Fetch page, generate metadata, insert    |
   | `clawsqlite knowledge search`| Search articles with category/priority   |
   | `clawsqlite knowledge show`  | Show a single article                    |
   | `clawsqlite knowledge update`| Update article fields                    |
   | `clawsqlite knowledge delete`| Delete article + content                 |
   | `clawsqlite knowledge reindex`| Rebuild FTS/vec indexes                 |
   | `clawsqlite knowledge maintenance`| Cleanup files + DB + indexes        |
   | ...                          |                                          |

2. For each command, classify it as:

   - **plumbing wrapper candidate** (can be re-expressed using `db/index/fs`), or
   - **application-level** (knowledge-specific business logic).

This inventory table should live in a separate doc, e.g.
`docs/clawsqlite-knowledge-commands.md`.

---

### 5.2 Step 1 – Establish CLI namespaces (no behavior change)

Goal: introduce the new top-level structure without changing behavior.

Actions:

1. Add a new top-level command group:

   ```bash
   clawsqlite knowledge ...
   ```

   Internally, this can initially delegate to the existing `kb`
   implementation so that:

   ```bash
   clawsqlite knowledge search
   ```

   behaves the same as:

   ```bash
   clawsqlite kb search
   ```

2. Decide whether to keep `kb` as a short alias for power users, but treat
   `knowledge` as the **primary** namespace in docs and skills.

3. Reserve the other namespaces (no implementation yet):

   ```bash
   clawsqlite db ...
   clawsqlite index ...
   clawsqlite fs ...
   clawsqlite reading ...   # future
   ```

---

### 5.3 Step 2 – Implement minimal plumbing commands

Goal: get the **core plumbing** commands working so application commands
can start wrapping them.

Recommended initial set:

- `clawsqlite db`:
  - `db schema`
  - `db exec`
  - `db vacuum`
  - `db backup`
  - (optional at first) `db analyze`

- `clawsqlite index`:
  - `index check`
  - `index rebuild`
  - (optional at first) `index search`

- `clawsqlite fs`:
  - `fs list-orphans`
  - `fs gc`
  - (optional) `fs reconcile`

Checklist for this step:

1. Implement each command with a narrow, testable behavior.
2. Add examples to the doc (this file) and smoke tests / examples under
   `examples/`.
3. Ensure they do not depend on KB-specific schema beyond the parameters
   provided (`--table`, `--fts-col`, `--vec-col`, `--path-col`, etc.).

---

### 5.4 Step 3 – Migrate knowledge commands to use plumbing

Goal: refactor existing KB commands to call `db/index/fs` internally.

Examples:

- `knowledge reindex`:

  - Before: custom Python code that directly rebuilds FTS/vec indexes.
  - After: wrapper around `index check` + `index rebuild` with fixed
    `--table`/`--fts-col`/`--vec-col`.

- `knowledge maintenance`:

  - Before: custom combo of file cleanup + DB cleanup + index checks.
  - After: wrapper around:

    ```bash
    clawsqlite fs gc ...
    clawsqlite db vacuum ...
    clawsqlite index check ...
    ```

Notes:

- `knowledge ingest`, `knowledge update`, `knowledge delete`, `knowledge show`
  remain **application-level**; they will call plumbing commands as needed
  (e.g. `db exec` or `index rebuild`), but their semantics stay in the
  `knowledge` namespace.

- The goal is **not** to force every action into plumbing, but to avoid
  duplicating generic DB/FS/index logic in each app.

---

### 5.5 Step 4 – Documentation and skill alignment

Goal: align docs and OpenClaw skills with the new structure.

Actions:

1. Update `README.md` / `README_zh.md` to:
   - introduce `clawsqlite` as the main CLI;
   - describe the namespaces: `db`, `index`, `fs`, `knowledge`, `reading`.

2. For ClawHub skills:

   - Existing KB skill should:
     - install `clawsqlite` from PyPI;
     - call `clawsqlite knowledge ...` commands.

   - Future reading skill should:
     - also install the same `clawsqlite` package;
     - call `clawsqlite reading ...`.

3. Make sure examples in skill READMEs use the **application-level
   namespaces** (`knowledge`, `reading`), not the plumbing ones. Plumbing
   commands are for advanced users and other apps.

With this roadmap documented, we can track progress step by step and avoid
losing pieces during refactor.
