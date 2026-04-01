# Changelog

All notable changes to this project will be documented in this file.

Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
without strict version tagging yet. Entries are grouped by date + topic.

---

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

- **ENV.example** now documents the interest clustering knobs:
  - `CLAWSQLITE_INTEREST_MERGE_DISTANCE` (cosine distance threshold between
    centroids; typical values ~0.07),
  - `CLAWSQLITE_INTEREST_MERGE_ALPHA` (used by analysis scripts to suggest
    a merge distance based on median mean_radius).

- **README / README_zh** updated to describe:
  - Two-stage interest clustering design.
  - How to configure `CLAWSQLITE_INTEREST_*` variables.
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
