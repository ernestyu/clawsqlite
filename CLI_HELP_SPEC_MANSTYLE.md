# clawsqlite-knowledge(1)

## NAME
`clawsqlite knowledge` - Markdown + SQLite knowledge base CLI (FTS5; optional sqlite-vec).

## SYNOPSIS
```bash
clawsqlite knowledge [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]
python -m clawsqlite_cli knowledge [GLOBAL OPTIONS] <command> [COMMAND OPTIONS]
```

## DESCRIPTION
- Stores metadata in SQLite and content in Markdown files under an articles directory.
- Uses FTS5 for full-text search; uses sqlite-vec for vector search when embeddings + vec0 are available.
- Auto-loads a project-level `.env` from the current working directory; project `.env` overrides existing environment variables (CLI > .env > process env).
- When embeddings are not available, `search --mode hybrid` falls back to FTS-only and prints a `NEXT:` hint; `--mode vec` errors with a `NEXT:` hint.

## HELP (ARGPARSE)

### global
```text
usage: clawsqlite knowledge [-h] [--root ROOT] [--db DB]
                            [--articles-dir ARTICLES_DIR]
                            [--tokenizer-ext TOKENIZER_EXT]
                            [--vec-ext VEC_EXT] [--json] [--verbose]
                            {build-interest-clusters,ingest,search,show,export,update,delete,reindex,inspect-interest-clusters,embed-from-summary,maintenance,doctor} ...

OpenClaw knowledge base CLI (SQLite + FTS5 + sqlite-vec).

positional arguments:
  {build-interest-clusters,ingest,search,show,export,update,delete,reindex,inspect-interest-clusters,embed-from-summary,maintenance,doctor}
    build-interest-clusters
                        Build interest clusters from existing article
                        embeddings
    ingest              Ingest a URL or a text into the KB
    search              Search the KB (fts/vec/hybrid)
    show                Show one record
    export              Export one record to file
    update              Update one record (patch or regen)
    delete              Delete one record (soft by default)
    reindex             Maintenance: check/fix/rebuild
    inspect-interest-clusters
                        Inspect interest cluster radius + PCA scatter plot
    embed-from-summary  Embed article summaries into articles_vec via plumbing
    maintenance         Maintenance: prune orphan/backup files and check paths
    doctor              Self-check knowledge DB/env, output JSON report

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
```

### build-interest-clusters
```text
usage: clawsqlite knowledge build-interest-clusters [-h] [--root ROOT]
                                                    [--db DB]
                                                    [--articles-dir ARTICLES_DIR]
                                                    [--tokenizer-ext TOKENIZER_EXT]
                                                    [--vec-ext VEC_EXT]
                                                    [--json] [--verbose]
                                                    [--algo {kmeans++,hierarchical}]
                                                    [--tag-weight TAG_WEIGHT]
                                                    [--use-pca] [--no-pca]
                                                    [--pca-explained-variance-threshold PCA_EXPLAINED_VARIANCE_THRESHOLD]
                                                    [--min-size MIN_SIZE]
                                                    [--max-clusters MAX_CLUSTERS]
                                                    [--kmeans-random-state KMEANS_RANDOM_STATE]
                                                    [--kmeans-n-init KMEANS_N_INIT]
                                                    [--kmeans-max-iter KMEANS_MAX_ITER]
                                                    [--enable-post-merge]
                                                    [--disable-post-merge]
                                                    [--merge-distance-threshold MERGE_DISTANCE_THRESHOLD]
                                                    [--hierarchical-distance-threshold HIERARCHICAL_DISTANCE_THRESHOLD]
                                                    [--hierarchical-linkage {average,complete}]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --algo {kmeans++,hierarchical}
                        Clustering backend (default from env or kmeans++)
  --tag-weight TAG_WEIGHT
                        Weight of tag_vec in interest-vector mix, range [0,1]
  --use-pca             Enable PCA before clustering
  --no-pca              Disable PCA and cluster in original vector space
  --pca-explained-variance-threshold PCA_EXPLAINED_VARIANCE_THRESHOLD
                        PCA cumulative explained variance threshold (e.g.
                        0.90, 0.95)
  --min-size, --min-cluster-size MIN_SIZE
                        Minimum cluster size
  --max-clusters MAX_CLUSTERS
                        Maximum initial clusters (kmeans++)
  --kmeans-random-state KMEANS_RANDOM_STATE
                        Random seed for kmeans++
  --kmeans-n-init KMEANS_N_INIT
                        Number of kmeans++ restarts
  --kmeans-max-iter KMEANS_MAX_ITER
                        Max iterations per kmeans++ run
  --enable-post-merge   Enable post-merge of close clusters (kmeans++)
  --disable-post-merge  Disable post-merge of close clusters (kmeans++)
  --merge-distance-threshold MERGE_DISTANCE_THRESHOLD
                        Post-merge cosine-distance threshold (kmeans++)
  --hierarchical-distance-threshold HIERARCHICAL_DISTANCE_THRESHOLD
                        Distance threshold used to cut hierarchical tree
  --hierarchical-linkage {average,complete}
                        Hierarchical linkage strategy
```

### inspect-interest-clusters
```text
usage: clawsqlite knowledge inspect-interest-clusters [-h] [--root ROOT]
                                                      [--db DB]
                                                      [--articles-dir ARTICLES_DIR]
                                                      [--tokenizer-ext TOKENIZER_EXT]
                                                      [--vec-ext VEC_EXT]
                                                      [--json] [--verbose]
                                                      [--vec-dim VEC_DIM]
                                                      [--no-plot]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --vec-dim VEC_DIM     Embedding dimension (optional, default:
                        CLAWSQLITE_VEC_DIM / auto)
  --no-plot             Only print stats, do not generate PNG plot
```

### delete
```text
usage: clawsqlite knowledge delete [-h] [--root ROOT] [--db DB]
                                   [--articles-dir ARTICLES_DIR]
                                   [--tokenizer-ext TOKENIZER_EXT]
                                   [--vec-ext VEC_EXT] [--json] [--verbose]
                                   --id ID [--hard] [--remove-file]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --id ID               Article id
  --hard                Hard delete (remove db row)
  --remove-file         When hard delete, permanently remove markdown file (no
                        backup)
```

### embed-from-summary
```text
usage: clawsqlite knowledge embed-from-summary [-h] [--root ROOT] [--db DB]
                                               [--articles-dir ARTICLES_DIR]
                                               [--tokenizer-ext TOKENIZER_EXT]
                                               [--vec-ext VEC_EXT] [--json]
                                               [--verbose] [--where WHERE]
                                               [--limit LIMIT]
                                               [--offset OFFSET]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --where WHERE         Optional SQL WHERE clause on articles (default:
                        undeleted with non-empty summary)
  --limit LIMIT         Optional LIMIT for batching
  --offset OFFSET       Optional OFFSET for batching
```

... (other subcommands unchanged)


### report-interest
```text
usage: clawsqlite knowledge report-interest [-h] [--root ROOT] [--db DB]
                                            [--articles-dir ARTICLES_DIR]
                                            [--tokenizer-ext TOKENIZER_EXT]
                                            [--vec-ext VEC_EXT] [--json]
                                            [--verbose] [--days DAYS]
                                            [--from DATE_FROM] [--to DATE_TO]
                                            [--vec-dim VEC_DIM]
                                            [--out-dir OUT_DIR] [--lang LANG]
                                            [--format {md,html}] [--no-pdf]

options:
  -h, --help            show this help message and exit
  --root ROOT           Root dir. Priority: CLI --root > $CLAWSQLITE_ROOT >
                        $CLAWSQLITE_ROOT_DEFAULT > <cwd>/knowledge_data.
  --db DB               SQLite db path. Priority: CLI --db > $CLAWSQLITE_DB >
                        <root>/knowledge.sqlite3
  --articles-dir ARTICLES_DIR
                        Articles markdown dir. Priority: CLI --articles-dir >
                        $CLAWSQLITE_ARTICLES_DIR > <root>/articles
  --tokenizer-ext TOKENIZER_EXT
                        Tokenizer extension path. Default:
                        /usr/local/lib/libsimple.so or
                        $CLAWSQLITE_TOKENIZER_EXT
  --vec-ext VEC_EXT     vec0 extension path. Default: auto-discover or
                        $CLAWSQLITE_VEC_EXT
  --json                Output JSON
  --verbose             Verbose logging
  --days DAYS           Lookback window in days (ignored if --from/--to
                        provided)
  --from DATE_FROM      Start date (YYYY-MM-DD)
  --to DATE_TO          End date (YYYY-MM-DD, exclusive)
  --vec-dim VEC_DIM     Embedding dimension (optional, default:
                        CLAWSQLITE_VEC_DIM / auto)
  --out-dir OUT_DIR     Root directory for reports (default: ./reports)
  --lang LANG           Report language (en/zh). Default:
                        $CLAWSQLITE_REPORT_LANG or en
  --format {md,html}    Additional output format: 'md' (default) or 'html'
                        (also write report.html via pandoc)
  --no-pdf              Do not run pandoc to generate PDF

```
