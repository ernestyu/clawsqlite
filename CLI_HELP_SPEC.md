# CLI Help Spec (generated from argparse)

## clawkb --help
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


## clawkb ingest --help
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


## clawkb search --help
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


## clawkb show --help
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


## clawkb update --help
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


## clawkb delete --help
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


## clawkb export --help
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


## clawkb reindex --help
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

