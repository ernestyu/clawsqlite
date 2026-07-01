# Changelog

All notable changes to this project will be documented in this file.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
without strict version tagging yet. Entries are grouped by date + topic.

---

## 2026-07-01 – Strict Config Contract Closure

### Changed

- Top-level CLI now exposes `clawsqlite knowledge ...` and
  `clawsqlite admin ...`; low-level DB/index/fs/embed primitives moved under
  `clawsqlite admin db/index/fs/embed ...`.
- `clawsqlite admin ...` now shares the same component-root `clawsqlite.toml`
  as `clawsqlite knowledge ...`. Admin DB/root/table flags are explicit
  debug/recovery overrides; the normal path uses the configured knowledge DB,
  articles directory, and runtime service settings.
- Admin commands now fail more cleanly: vec0-dependent index checks/rebuilds
  warn instead of tracebacking when sqlite-vec is unavailable, FTS rebuild can
  use explicit `--fts-cols`, filesystem GC handles rowid aliases correctly,
  inline `admin db exec` queries can print TSV/JSON results, and embedding
  service failures are wrapped with operator-facing `ERROR`/`NEXT` hints.
- Replaced the internal `SMALL_LLM_*` runtime names with `LLM_BASE_URL`,
  `LLM_MODEL`, and `LLM_API_KEY`. The project no longer keeps a compatibility
  path for the old names.
- Tightened short-summary passthrough: direct text and short
  `note`/`thought`/`discussion_summary` content can preserve cleaned text, while
  URL/web-article ingest still asks the LLM to summarize.
- Removed `--tags` from `knowledge ingest`; manual ingest tags are now expressed
  only as `--tags-hint`. The `update --tags` patch operation remains separate.
- Replaced ingest JSON `embedding_enabled` with clearer
  `embedding_runtime_enabled` and `embedding_required` fields.
- Doctor config output now reports why the active `clawsqlite.toml` was chosen
  through `config_resolution_mode` and `config_source_reason`.
- Added pytest configuration and a development optional dependency so running
  `pytest` from the repository root is a supported test path.

### Tests

- Fixed tests that confused `require_embedding = false` with embedding runtime
  availability.
- Added coverage for short URL/web-article summary behavior and config
  resolution metadata.

---

## 2026-06-30 – Single Config File Cleanup

### Removed

- Removed the legacy Agent usage note from the repository root.
- Removed the separate environment example file. `clawsqlite.toml` is the only
  project configuration file; runtime hooks that still use process environment
  variables are not documented as a second config source.

### Changed

- Documentation now distinguishes `clawsqlite_knowledge/` (Python
  implementation) from `skills/clawsqlite-knowledge/` (thin Agent-facing skill
  instructions).
- The Knowledge CLI no longer auto-loads dot-env files.
- Search, FTS, interest clustering, and report defaults that were formerly
  documented in the separate environment example are now represented in
  `clawsqlite.toml.example` and the `init-config` template.

---

## 2026-04-05 – Search Recall Tiers & Vector Normalization

### Added

- **Four-tier search capability modes** (auto-selected at query time):
  - Mode1: LLM + Embedding
  - Mode2: LLM + no Embedding
  - Mode3: no LLM + Embedding
  - Mode4: no LLM + no Embedding

- **Query planning for search**: build `query_refine` + `query_tags` (LLM when enabled; heuristic fallback otherwise).

- **Mode-specific score weight overrides** via env:
  - `CLAWSQLITE_SCORE_WEIGHTS_MODE1..MODE4`
  - `CLAWSQLITE_SCORE_WEIGHTS_TEXT` (for text-only modes)

### Changed

- **Vector storage & scoring semantics**:
  - All stored vectors and query vectors are now **L2-normalized** before use.
  - Semantic scoring uses **cosine similarity mapped to [0,1]**, with a distance-based fallback for older/partial vec rows.

- **Tag vector maintenance**:
  - `ingest` / `update` now keep `articles_tag_vec` in sync (when embeddings are enabled), in addition to `articles_vec`.

- **Embedding admin primitive** (`clawsqlite admin embed column`) now
  L2-normalizes vectors before writing to vec tables.

- **Documentation** updated:
  - Historical search-mode docs were expanded with the new knobs. The later
    config cleanup removed the separate environment template in favor of
    `clawsqlite.toml` as the only project config file.
  - `README.md` / `README_zh.md` updated to match the new search modes and scoring.

## 2026-04-05 – Interest Cluster Builder Refactor (kmeans++ / hierarchical / PCA)

### Added

- **Unified interest-cluster pipeline** in `clawsqlite_knowledge.interest`:
  - Data loading (`articles` + `articles_vec` + `articles_tag_vec`)
  - Interest-vector construction with strict branch-wise L2 normalization:
    - `summary_vec` and `tag_vec` are each L2-normalized first
    - weighted mix by `tag_weight`
    - final L2 normalization of the mixed vector
  - Optional PCA with **auto dimension selection** from cumulative explained variance threshold.
  - Two algorithm backends:
    - `kmeans++`
    - `hierarchical` (distance-threshold cut; `average`/`complete` linkage)
  - Unified postprocessing:
    - small-cluster reassignment in original 1024-d space
    - optional post-merge for `kmeans++`
  - Stable final cluster renumbering rule:
    - by cluster size desc, then min `article_id`.

- **New build-interest-clusters CLI options**:
  - `--algo`
  - `--tag-weight`
  - `--use-pca` / `--no-pca`
  - `--pca-explained-variance-threshold`
  - `--min-size` / `--min-cluster-size`
  - `--max-clusters`
  - `--kmeans-random-state`
  - `--kmeans-n-init`
  - `--kmeans-max-iter`
  - `--enable-post-merge` / `--disable-post-merge`
  - `--merge-distance-threshold`
  - `--hierarchical-distance-threshold`
  - `--hierarchical-linkage`

### Changed

- `resolve_interest_params` now resolves the full clustering parameter set
  from CLI/env/defaults (not just `min_size/max_clusters`).

- `build-interest-clusters` no longer requires live embedding API keys;
  it works from stored vectors in DB.

- Final metadata in `interest_meta` now includes:
  - `interest_cluster_algo`
  - `interest_cluster_use_pca`
  - `interest_cluster_pca_variance_threshold`
  - `interest_cluster_pca_dim`
  - `interest_cluster_pca_explained_variance`
  - `interest_cluster_tag_weight`

- Analysis helpers now rebuild vectors through shared clustering logic, so
  quality inspection scripts and cluster builder use the same vector pipeline.

### Docs

- Historical docs were updated with a full recommended interest-clustering
  template. The later config cleanup removed the separate environment template
  in favor of `clawsqlite.toml` as the only project config file.
- Updated `README.md` and `README_zh.md` to document:
  - new backends and parameters
  - PCA behavior and 1024-d centroid invariant
  - unified postprocessing and persistence behavior.

## 2026-03-31 / 2026-04-01 – Interest Clustering & Analysis

### Added

- **Two-stage interest clustering** in `clawsqlite_knowledge.interest`:
  - Build "interest vectors" from summary + tag embeddings for undeleted
    articles with non-empty summaries.
  - First-stage k-means with high `k0`, followed by `min_size`-based
    reassignment of tiny clusters to their nearest large cluster.
  - Second-stage clustering:
    - Compute centroid-to-centroid cosine distances (1 - cos sim).
    - Use a union-find to merge clusters whose centroids are closer than
      `CLAWSQLITE_INTEREST_MERGE_DISTANCE`.
    - Use merged centroids as initialization for a second k-means run with
      reduced `k'`.
    - Write final clusters into `interest_clusters` and
      `interest_cluster_members`.

- **Interest clustering CLI commands**:
  - `clawsqlite knowledge build-interest-clusters`:
    - Parameters:
      - `--min-size` / `CLAWSQLITE_INTEREST_MIN_SIZE` (default 5)
      - `--max-clusters` / `CLAWSQLITE_INTEREST_MAX_CLUSTERS` (default 16)
    - Builds or rebuilds interest clusters for an existing KB.
  - `clawsqlite knowledge inspect-interest-clusters`:
    - Prints per-cluster radius stats:
      - `size / n_members / mean_radius / max_radius` (1 - cos distance).
    - Prints pairwise centroid distances: `min / max / median`.
    - Optionally (when matplotlib is available) generates a PCA 2D scatter
      plot of centroids with:
      - point size ∝ cluster size (sqrt-scaled),
      - point color ∝ `mean_radius` (viridis colormap),
      - saved to `./interest_clusters_pca.png`.
    - Flags:
      - `--vec-dim` (optional; defaults to `CLAWSQLITE_VEC_DIM` / auto),
      - `--no-plot` to skip PNG generation.

- **Interest inspection helpers module**:
  - `clawsqlite_knowledge.inspect_interest.inspect_interest_clusters()`
    encapsulates the analysis logic used by the CLI and tests.

- **Analysis scripts (tests)**:
  - `tests/test_interest_cluster_quality.py`:
    - Rebuilds interest vectors and prints radius + centroid distance stats.
    - Generates `tests/interest_clusters_pca.png` with size + mean_radius
      encoding for debugging.
  - `tests/test_interest_merge_suggest.py`:
    - Computes per-cluster `mean_radius` and suggests a
      `CLAWSQLITE_INTEREST_MERGE_DISTANCE` via:
      - `merge_distance ≈ alpha * median(mean_radius)`
      - with `alpha` defaulting to 0.4 (configurable via CLI `--alpha` or
        env `CLAWSQLITE_INTEREST_MERGE_ALPHA`).

### Changed

- **Interest clustering configuration** is now resolved via a shared helper
  (`resolve_interest_params` in `clawsqlite_knowledge.utils`), with a
  consistent priority chain:
  1. CLI parameters (`--min-size`, `--max-clusters`, future `--alpha`),
  2. Environment variables:
     - `CLAWSQLITE_INTEREST_MIN_SIZE`
     - `CLAWSQLITE_INTEREST_MAX_CLUSTERS`
     - `CLAWSQLITE_INTEREST_MERGE_ALPHA`
  3. Hard-coded defaults (5 / 16 / 0.4).

- Historical environment-template docs covered the interest clustering knobs:
  - `CLAWSQLITE_INTEREST_MERGE_DISTANCE` (cosine distance threshold between
    centroids; typical values ~0.07),
  - `CLAWSQLITE_INTEREST_MERGE_ALPHA` (used by analysis scripts to suggest
    a merge distance based on median mean_radius).

- **README / README_zh** updated to describe:
  - Two-stage interest clustering design.
  - How interest clustering knobs were configured at that time.
  - How to run `build-interest-clusters` and
    `inspect-interest-clusters` in real KBs.
  - Recommended workflow for tuning `min_size`, `max_clusters` and
    `merge_distance` using the analysis tools.

### Notes

- The new interest clustering & analysis features are currently implemented
  on branch `bot/20260331-clawsqlite-cluster-inspect`. They are intended for
  use by higher-level tools such as personal RSS/"radar" workflows that
  need stable, interpretable topic clusters over the knowledge base.

- Future releases may:
  - Add a dedicated CLI to run the merge-distance suggestion logic
    (wrapping `test_interest_merge_suggest.py`),
  - Introduce versioned changelog entries with explicit tags
    (e.g. `v0.2.0`), and
  - Tighten invariants on final centroid spacing (e.g. enforcing
    `min pairwise centroid distance >= MERGE_DISTANCE` post second-stage
    k-means).
