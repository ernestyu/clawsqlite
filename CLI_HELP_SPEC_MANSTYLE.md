# clawkb(1)

## NAME
**clawkb** — OpenClaw 知识库命令行：SQLite + FTS5 + sqlite-vec（可选）

## SYNOPSIS
```bash
clawkb [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]
python -m clawkb [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]
```

## DESCRIPTION
clawkb 用一个 SQLite 文件管理你的个人知识库。每条记录对应一篇文章（或一段文本），正文保存在 Markdown 文件中；数据库保存元信息（title/tags/summary/category/时间戳/路径/priority），并提供两类索引。

FTS5 全文检索索引用于通过 title/tags/summary 召回候选；sqlite-vec 向量索引用于用 embedding 做 KNN 召回，再和 FTS 结果做混合排序。

当 tokenizer 或 vec0 扩展不存在时，系统会自动降级：FTS 会用 SQLite 默认 tokenizer；vec 表会跳过创建，向量相关功能会自动失效（不会影响基础入库与展示）。

## GLOBAL OPTIONS
这部分参数既可以放在主命令前，也可以放在子命令后。

```text
usage: clawkb [-h] [--root ROOT] [--db DB] [--articles-dir ARTICLES_DIR]
              [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT] [--json]
              [--verbose]
              {ingest,search,show,export,update,delete,reindex} ...

OpenClaw knowledge base CLI (SQLite + FTS5 + sqlite-vec).

positional arguments:
  {ingest,search,show,export,update,delete,reindex}
    ingest              Ingest a URL or a text into the KB
    search              Search the KB (fts/vec/hybrid)
    show                Show one record
    export              Export one record to file
    update              Update one record (patch or regen)
    delete              Delete one record (soft by default)
    reindex             Maintenance: check/fix/rebuild

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
```

## ENVIRONMENT
clawkb 会读取这些环境变量作为默认值（如果命令行参数没有显式给出）：

- `CLAWKB_ROOT`：根目录，默认 `/home/node/.openclaw/workspace/clawkb`
- `CLAWKB_DB`：SQLite 文件路径，默认 `<root>/clawkb.sqlite3`
- `CLAWKB_ARTICLES_DIR`：Markdown 存放目录，默认 `<root>/articles`
- `CLAWKB_TOKENIZER_EXT`：simple tokenizer 扩展路径，默认 `/usr/local/lib/libsimple.so`（设为 `none` 表示不加载）
- `CLAWKB_VEC_EXT`：vec0 扩展路径，默认自动探测（设为 `none` 表示不加载）

Embedding 与小模型相关配置本项目不强制要求；当 embedding 的 base url / api key 等环境变量不存在时，embedding 会自动禁用。

## COMMANDS
以下每个子命令给出：说明、参数、以及集中示例。

---

## ingest(1)

### NAME
**clawkb ingest** — 导入一条记录（URL 或文本）

### SYNOPSIS
```bash
clawkb ingest (--url URL | --text TEXT) [options]
```

### DESCRIPTION
导入流程是：获取正文内容（抓取 URL 或使用纯文本）→ 生成/补齐 title/summary/tags（可选）→ 写入 SQLite → 写入 Markdown 文件 → 同步 FTS/vec 索引（能用就用，不能用就跳过）。

### OPTIONS
```text
usage: clawkb ingest [-h] [--root ROOT] [--db DB]
                     [--articles-dir ARTICLES_DIR]
                     [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                     [--json] [--verbose] (--url URL | --text TEXT)
                     [--title TITLE] [--summary SUMMARY] [--tags TAGS]
                     [--category CATEGORY] [--priority PRIORITY]
                     [--gen-provider {openclaw,llm,off}]
                     [--max-summary-chars MAX_SUMMARY_CHARS]
                     [--scrape-cmd SCRAPE_CMD]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --url URL             URL to ingest
  --text TEXT           Raw text content to ingest
  --title TITLE         Title override
  --summary SUMMARY     Summary override (long summary)
  --tags TAGS           Tags override (comma-separated)
  --category CATEGORY   Category, e.g. web/github/story
  --priority PRIORITY   Priority (0 default)
  --gen-provider {openclaw,llm,off}
                        Field generator provider
  --max-summary-chars MAX_SUMMARY_CHARS
                        Hard limit for summary length (chars)
  --scrape-cmd SCRAPE_CMD
                        Scraper command for URL ingest. Or env
                        CLAWKB_SCRAPE_CMD
```

### EXAMPLES
```bash
# 导入网页
clawkb ingest --url "https://example.com/a" --category web --root /home/node/.openclaw/workspace/clawkb --json

# 导入一段文本
clawkb ingest --text "这里是一段文本……" --category note --tags "ai,rag" --json

# 关闭字段生成（完全手动提供）
clawkb ingest --text "..." --gen-provider off --title "手动标题" --summary "手动摘要" --tags "x,y"

# 指定抓取脚本命令（URL 模式）
clawkb ingest --url "https://example.com" --scrape-cmd "/home/node/.openclaw/workspace/scripts/scrape.sh {url}"
```

---

## search(1)

### NAME
**clawkb search** — 检索（fts/vec/hybrid）

### SYNOPSIS
```bash
clawkb search QUERY [--mode fts|vec|hybrid] [options]
```

### DESCRIPTION
search 支持三种模式：fts（只走 FTS5）、vec（只走向量召回，需要 embedding + vec0）、hybrid（默认，混合召回与排序）。你也可以加过滤条件（category、tag、since、priority、include-deleted）来缩小范围。

### OPTIONS
```text
usage: clawkb search [-h] [--root ROOT] [--db DB]
                     [--articles-dir ARTICLES_DIR]
                     [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                     [--json] [--verbose] [--mode {hybrid,fts,vec}]
                     [--topk TOPK] [--candidates CANDIDATES]
                     [--llm-keywords {auto,on,off}]
                     [--gen-provider {openclaw,llm,off}] [--category CATEGORY]
                     [--tag TAG] [--since SINCE] [--priority PRIORITY]
                     [--include-deleted]
                     query

positional arguments:
  query                 Query text

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --mode {hybrid,fts,vec}
                        Search mode
  --topk TOPK           Number of results to return
  --candidates CANDIDATES
                        Candidate pool size before final ranking
  --llm-keywords {auto,on,off}
                        Keyword expansion policy for FTS
  --gen-provider {openclaw,llm,off}
                        Keyword generator provider (used when llm-
                        keywords=auto/on)
  --category CATEGORY   Filter by category
  --tag TAG             Filter by tag substring
  --since SINCE         Filter created_at >= since (ISO, e.g.
                        2026-03-01T00:00:00Z)
  --priority PRIORITY   Priority filter, e.g. eq:0, gt:0, ge:1
  --include-deleted     Include deleted items
```

### EXAMPLES
```bash
# 默认混合检索
clawkb search "perpetual dex funding rate" --topk 10 --json

# 只用 FTS
clawkb search "融资费率" --mode fts --topk 20

# 只用 vec（前提是 embedding 已启用且 articles_vec 可用）
clawkb search "how to design a rag system" --mode vec --topk 10 --json

# 过滤：只看某个分类，且 created_at 在某日期之后
clawkb search "openclaw" --category github --since 2026-03-01T00:00:00Z

# 过滤：priority 规则（例如只看 priority>0）
clawkb search "read later" --priority gt:0
```

---

## show(1)

### NAME
**clawkb show** — 显示一条记录

### SYNOPSIS
```bash
clawkb show --id ID [--full] [--json]
```

### DESCRIPTION
show 用来查看数据库字段；加 `--full` 会把 Markdown 文件内容一起输出（如果文件存在）。

### OPTIONS
```text
usage: clawkb show [-h] [--root ROOT] [--db DB] [--articles-dir ARTICLES_DIR]
                   [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                   [--json] [--verbose] --id ID [--full]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --id ID               Article id
  --full                Include markdown content
```

### EXAMPLES
```bash
clawkb show --id 12
clawkb show --id 12 --full --json
```

---

## update(1)

### NAME
**clawkb update** — 更新一条记录（patch 或 regen）

### SYNOPSIS
```bash
clawkb update --id ID [patch options] [--regen ...]
```

### DESCRIPTION
update 支持 patch（显式给字段）和 regen（重新生成 title/summary/tags）。更新后会同步 FTS；如果 embedding 可用且 summary 非空，会同步 vec；否则会尝试删除 vec 中该条向量行（如果存在）。

### OPTIONS
```text
usage: clawkb update [-h] [--root ROOT] [--db DB]
                     [--articles-dir ARTICLES_DIR]
                     [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                     [--json] [--verbose] --id ID [--title TITLE]
                     [--summary SUMMARY] [--tags TAGS] [--category CATEGORY]
                     [--priority PRIORITY]
                     [--regen {title,summary,tags,embedding,all}]
                     [--gen-provider {openclaw,llm,off}]
                     [--max-summary-chars MAX_SUMMARY_CHARS]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --id ID               Article id
  --title TITLE         Patch: new title
  --summary SUMMARY     Patch: new summary
  --tags TAGS           Patch: new tags (comma-separated)
  --category CATEGORY   Patch: new category
  --priority PRIORITY   Patch: new priority
  --regen {title,summary,tags,embedding,all}
                        Regenerate fields
  --gen-provider {openclaw,llm,off}
                        Generator provider for regen
  --max-summary-chars MAX_SUMMARY_CHARS
                        Hard limit for summary length (chars)
```

### EXAMPLES
```bash
# 手动修正 tags
clawkb update --id 12 --tags "rl,rag,sqlite"

# 重新生成摘要
clawkb update --id 12 --regen summary --gen-provider openclaw

# 同时 patch + regen
clawkb update --id 12 --title "新标题" --regen tags
```

---

## delete(1)

### NAME
**clawkb delete** — 删除一条记录（默认软删）

### SYNOPSIS
```bash
clawkb delete --id ID [--hard] [--remove-file]
```

### DESCRIPTION
默认是软删：写入 deleted_at，并从 FTS/vec 中移除索引行。`--hard` 会从 articles 表删除整行；加 `--remove-file` 会把 Markdown 文件也删掉（仅 hard 模式下有效）。

### OPTIONS
```text
usage: clawkb delete [-h] [--root ROOT] [--db DB]
                     [--articles-dir ARTICLES_DIR]
                     [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                     [--json] [--verbose] --id ID [--hard] [--remove-file]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --id ID               Article id
  --hard                Hard delete (remove db row)
  --remove-file         When hard delete, also remove markdown file
```

### EXAMPLES
```bash
# 软删
clawkb delete --id 12

# 硬删并移除正文文件
clawkb delete --id 12 --hard --remove-file
```

---

## export(1)

### NAME
**clawkb export** — 导出一条记录到文件

### SYNOPSIS
```bash
clawkb export --id ID --format md|json --out PATH [--full]
```

### DESCRIPTION
export 是“写到文件”的版本：可导出 md 或 json。`--full` 会把 Markdown 正文也导出（json 中会放在 content 字段；md 中则输出完整正文）。

### OPTIONS
```text
usage: clawkb export [-h] [--root ROOT] [--db DB]
                     [--articles-dir ARTICLES_DIR]
                     [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                     [--json] [--verbose] --id ID [--format {md,json}] --out
                     OUT [--full]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --id ID               Article id
  --format {md,json}    Export format
  --out OUT             Output file path
  --full                Export full markdown content
```

### EXAMPLES
```bash
# 导出元信息摘要到 md
clawkb export --id 12 --format md --out /tmp/a.md

# 导出完整记录到 json
clawkb export --id 12 --format json --out /tmp/a.json --full
```

---

## reindex(1)

### NAME
**clawkb reindex** — 日常维护：检查 / 修复 / 重建索引

### SYNOPSIS
```bash
clawkb reindex --check
clawkb reindex --fix-missing [--gen-provider ...]
clawkb reindex --rebuild [--fts] [--vec]
```

### DESCRIPTION
reindex 面向“每日自查”：`--check` 做统计；`--fix-missing` 补齐缺失字段与索引；`--rebuild` 重建索引。`--vec` 需要 embedding 可用；否则会失败或跳过（取决于运行时环境）。

### OPTIONS
```text
usage: clawkb reindex [-h] [--root ROOT] [--db DB]
                      [--articles-dir ARTICLES_DIR]
                      [--tokenizer-ext TOKENIZER_EXT] [--vec-ext VEC_EXT]
                      [--json] [--verbose] [--check] [--fix-missing]
                      [--rebuild] [--fts] [--vec]
                      [--gen-provider {openclaw,llm,off}]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Default:
                        /home/node/.openclaw/workspace/clawkb or $CLAWKB_ROOT
  --db DB               SQLite db path. Default: <root>/clawkb.sqlite3 or
                        $CLAWKB_DB
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Default: <root>/articles or
                        $CLAWKB_ARTICLES_DIR
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWKB_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --check               Check missing fields and index status
  --fix-missing         Fill missing fields and index rows
  --rebuild             Rebuild indexes
  --fts                 With --rebuild: rebuild FTS index
  --vec                 With --rebuild: rebuild vec index (requires embedding
                        enabled)
  --gen-provider {openclaw,llm,off}
                        Generator provider for fix-missing
```

### EXAMPLES
```bash
# 每日自查
clawkb reindex --check --json

# 自动补齐缺失字段与索引
clawkb reindex --fix-missing --gen-provider openclaw --verbose

# 重建 FTS
clawkb reindex --rebuild --fts

# 重建 vec（需要 embedding）
clawkb reindex --rebuild --vec
```

## EXIT STATUS
- 0：成功
- 2：参数或资源不存在（例如 id 不存在）
- 3：抓取失败（URL ingest）
- 4：运行期错误（数据库/索引/网络/生成等）

## FILES
- SQLite：`<root>/clawkb.sqlite3`
- Markdown：`<articles_dir>/<id>_<slug>.md`
