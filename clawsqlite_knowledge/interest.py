# -*- coding: utf-8 -*-
"""Interest cluster builder for clawsqlite_knowledge.

This module builds "interest clusters" from summary/tag embeddings in the KB.
It supports:

- kmeans++ backend
- hierarchical backend (distance-threshold cut)
- optional PCA (auto dimension via explained-variance threshold)

Hard invariant:
- Clustering may run in PCA space, but persisted centroids are always
  recomputed in the original 1024-d interest-vector space.
"""
from __future__ import annotations

import math
import os
import random
import sqlite3
from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, List, Tuple

from .utils import now_iso_z
from .embed import floats_to_f32_blob, _resolve_vec_dim, l2_normalize

try:  # optional dependency; used for PCA and scipy hierarchical path
    import numpy as _np
except Exception:  # pragma: no cover - optional
    _np = None  # type: ignore


@dataclass
class ArticleEmbeddingRecord:
    article_id: int
    title: str
    summary_vec: List[float] | None
    tag_vec: List[float] | None


@dataclass
class InterestClusterConfig:
    min_cluster_size: int = 5
    max_clusters: int = 16
    tag_weight: float = 0.75
    cluster_algo: str = "kmeans++"  # "kmeans++" | "hierarchical"
    use_pca: bool = True
    pca_explained_variance_threshold: float = 0.95
    kmeans_random_state: int = 42
    kmeans_n_init: int = 10
    kmeans_max_iter: int = 300
    enable_post_merge: bool = True
    merge_distance_threshold: float = 0.06
    hierarchical_distance_threshold: float = 0.20
    hierarchical_linkage: str = "average"  # "average" | "complete"


@dataclass
class BuildInterestClustersResult:
    article_ids: List[int]
    labels: List[int]
    cluster_members: Dict[int, List[int]]  # cluster_id -> article indices
    cluster_centroids_1024: Dict[int, List[float]]
    cluster_sizes: Dict[int, int]
    algo: str
    use_pca: bool
    pca_dim: int | None
    pca_explained_variance: float | None


@dataclass
class PCAOutput:
    vectors_cluster: List[List[float]]
    used: bool
    dim: int | None
    explained_variance: float | None
    note: str | None = None


def _ensure_interest_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
CREATE TABLE IF NOT EXISTS interest_clusters (
  id INTEGER PRIMARY KEY,
  label TEXT,
  size INTEGER NOT NULL,
  summary_centroid BLOB NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""
    )
    conn.execute(
        """
CREATE TABLE IF NOT EXISTS interest_cluster_members (
  cluster_id INTEGER NOT NULL,
  article_id INTEGER NOT NULL,
  membership REAL NOT NULL,
  PRIMARY KEY (cluster_id, article_id)
);
"""
    )
    conn.commit()


def _ensure_interest_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
CREATE TABLE IF NOT EXISTS interest_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""
    )
    conn.commit()


def _upsert_interest_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO interest_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _blob_to_floats(blob: bytes, dim: int) -> List[float]:
    import struct

    if len(blob) != 4 * dim:
        raise ValueError(f"Embedding blob size mismatch: got {len(blob)}, expected {4*dim}")
    return list(struct.unpack("<" + "f" * dim, blob))


def _squared_l2(a: List[float], b: List[float]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: List[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def _cosine_distance(a: List[float], b: List[float]) -> float:
    na = _norm(a)
    nb = _norm(b)
    if na <= 1e-12 or nb <= 1e-12:
        return 1.0
    sim = _dot(a, b) / (na * nb)
    if sim > 1.0:
        sim = 1.0
    elif sim < -1.0:
        sim = -1.0
    return 1.0 - sim


def _clip01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _normalize_cfg(cfg: InterestClusterConfig) -> InterestClusterConfig:
    algo = (cfg.cluster_algo or "kmeans++").strip().lower()
    if algo not in ("kmeans++", "hierarchical"):
        algo = "kmeans++"

    linkage = (cfg.hierarchical_linkage or "average").strip().lower()
    if linkage not in ("average", "complete"):
        linkage = "average"

    pca_thr = float(cfg.pca_explained_variance_threshold or 0.95)
    if pca_thr <= 0.0:
        pca_thr = 0.95
    if pca_thr > 1.0:
        pca_thr = 1.0

    merge_thr = float(cfg.merge_distance_threshold or 0.06)
    if merge_thr < 0.0:
        merge_thr = 0.0

    hier_thr = float(cfg.hierarchical_distance_threshold or 0.20)
    if hier_thr < 0.0:
        hier_thr = 0.0

    return InterestClusterConfig(
        min_cluster_size=max(1, int(cfg.min_cluster_size or 1)),
        max_clusters=max(1, int(cfg.max_clusters or 1)),
        tag_weight=_clip01(float(cfg.tag_weight)),
        cluster_algo=algo,
        use_pca=bool(cfg.use_pca),
        pca_explained_variance_threshold=pca_thr,
        kmeans_random_state=int(42 if cfg.kmeans_random_state is None else cfg.kmeans_random_state),
        kmeans_n_init=max(1, int(1 if cfg.kmeans_n_init is None else cfg.kmeans_n_init)),
        kmeans_max_iter=max(1, int(1 if cfg.kmeans_max_iter is None else cfg.kmeans_max_iter)),
        enable_post_merge=bool(cfg.enable_post_merge),
        merge_distance_threshold=merge_thr,
        hierarchical_distance_threshold=hier_thr,
        hierarchical_linkage=linkage,
    )


def _mean_vector_from_indices(vectors: List[List[float]], indices: List[int], dim: int) -> List[float]:
    if not indices:
        return [0.0] * dim
    acc = [0.0] * dim
    for idx in indices:
        v = vectors[idx]
        for d in range(dim):
            acc[d] += v[d]
    inv = 1.0 / float(len(indices))
    for d in range(dim):
        acc[d] *= inv
    return acc


def _labels_to_members(labels: List[int]) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    for idx, cid in enumerate(labels):
        out.setdefault(int(cid), []).append(idx)
    return out


def _size_stats(cluster_sizes: Dict[int, int]) -> Dict[str, int | float]:
    if not cluster_sizes:
        return {"max": 0, "min": 0, "median": 0.0}
    vals = sorted(cluster_sizes.values())
    return {
        "max": int(vals[-1]),
        "min": int(vals[0]),
        "median": float(median(vals)),
    }


def _clear_interest_tables(conn: sqlite3.Connection) -> None:
    _ensure_interest_tables(conn)
    conn.execute("DELETE FROM interest_cluster_members")
    conn.execute("DELETE FROM interest_clusters")
    conn.commit()


def load_articles_for_clustering(conn: sqlite3.Connection, *, dim: int) -> List[ArticleEmbeddingRecord]:
    rows = conn.execute(
        """
	SELECT a.id AS id,
	       coalesce(a.generated_title, '') AS title,
	       sv.embedding AS summary_embedding,
       tv.embedding AS tag_embedding
FROM articles a
LEFT JOIN articles_vec sv ON sv.id = a.id
LEFT JOIN articles_tag_vec tv ON tv.id = a.id
WHERE a.deleted_at IS NULL
  AND a.summary IS NOT NULL AND trim(a.summary) != ''
ORDER BY a.id ASC
"""
    ).fetchall()

    out: List[ArticleEmbeddingRecord] = []
    for r in rows:
        try:
            article_id = int(r["id"])
            title = str(r["title"] or "")
            sv_blob = r["summary_embedding"]
            tv_blob = r["tag_embedding"]
            if sv_blob is None and tv_blob is None:
                continue
            sv = _blob_to_floats(sv_blob, dim) if sv_blob is not None else None
            tv = _blob_to_floats(tv_blob, dim) if tv_blob is not None else None
            out.append(
                ArticleEmbeddingRecord(
                    article_id=article_id,
                    title=title,
                    summary_vec=sv,
                    tag_vec=tv,
                )
            )
        except Exception:
            # Best-effort: skip malformed rows.
            continue
    return out


def build_interest_vectors(
    records: List[ArticleEmbeddingRecord],
    *,
    dim: int,
    tag_weight: float,
) -> Tuple[List[int], List[List[float]], Dict[str, int]]:
    w_tag = _clip01(float(tag_weight))
    w_sum = 1.0 - w_tag

    article_ids: List[int] = []
    vectors_1024: List[List[float]] = []
    both_count = 0
    summary_only_count = 0
    tag_only_count = 0

    for rec in records:
        sv = rec.summary_vec
        tv = rec.tag_vec
        if sv is None and tv is None:
            continue

        if sv is not None and tv is not None:
            both_count += 1
            sv_n = l2_normalize(sv)
            tv_n = l2_normalize(tv)
            mixed = [0.0] * dim
            if w_sum > 0.0:
                for i in range(dim):
                    mixed[i] += w_sum * sv_n[i]
            if w_tag > 0.0:
                for i in range(dim):
                    mixed[i] += w_tag * tv_n[i]
            vec = l2_normalize(mixed)
        elif sv is not None:
            summary_only_count += 1
            vec = l2_normalize(sv)
        else:
            tag_only_count += 1
            vec = l2_normalize(tv or [])

        if len(vec) != dim:
            continue

        article_ids.append(rec.article_id)
        vectors_1024.append(vec)

    stats = {
        "both_vec_count": int(both_count),
        "summary_only_count": int(summary_only_count),
        "tag_only_count": int(tag_only_count),
        "eligible_articles": int(len(vectors_1024)),
    }
    return article_ids, vectors_1024, stats


def load_interest_vectors_from_db(
    conn: sqlite3.Connection,
    *,
    dim: int | None = None,
    tag_weight: float | None = None,
) -> Tuple[List[int], List[List[float]], Dict[str, int]]:
    d = int(dim or _resolve_vec_dim())
    if tag_weight is None:
        tag_weight = float(os.environ.get("CLAWSQLITE_INTEREST_TAG_WEIGHT", "0.75") or 0.75)
    records = load_articles_for_clustering(conn, dim=d)
    return build_interest_vectors(records, dim=d, tag_weight=tag_weight)


def fit_pca_if_enabled(
    vectors_1024: List[List[float]],
    *,
    use_pca: bool,
    explained_variance_threshold: float,
) -> PCAOutput:
    if not vectors_1024:
        return PCAOutput(vectors_cluster=[], used=False, dim=None, explained_variance=None, note="no_vectors")
    if not use_pca:
        return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="disabled")
    if len(vectors_1024) < 2:
        return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="too_few_samples")
    if _np is None:
        return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="numpy_missing")

    try:
        X = _np.asarray(vectors_1024, dtype=_np.float64)
        n_samples, n_features = X.shape
        if n_samples < 2 or n_features < 2:
            return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="degenerate_shape")

        X_centered = X - X.mean(axis=0, keepdims=True)
        _, s, vt = _np.linalg.svd(X_centered, full_matrices=False)
        if s.size == 0:
            return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="svd_empty")

        denom = float(max(1, n_samples - 1))
        eigvals = (s * s) / denom
        total = float(eigvals.sum())
        if total <= 1e-12:
            return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="zero_variance")

        var_ratio = eigvals / total
        cum = _np.cumsum(var_ratio)

        thr = float(explained_variance_threshold or 0.95)
        if thr <= 0.0:
            thr = 0.95
        if thr > 1.0:
            thr = 1.0

        k = int(_np.searchsorted(cum, thr, side="left") + 1)
        if k < 1:
            k = 1
        if k > vt.shape[0]:
            k = int(vt.shape[0])
        if k > n_features:
            k = int(n_features)

        X_cluster = X_centered @ vt[:k].T
        actual_var = float(cum[k - 1])
        return PCAOutput(
            vectors_cluster=X_cluster.astype(_np.float32).tolist(),
            used=True,
            dim=int(k),
            explained_variance=actual_var,
            note=None,
        )
    except Exception:
        return PCAOutput(vectors_cluster=vectors_1024, used=False, dim=None, explained_variance=None, note="pca_failed")


def _nearest_center_idx(point: List[float], centers: List[List[float]]) -> Tuple[int, float]:
    best_j = 0
    best_d = float("inf")
    for j, c in enumerate(centers):
        d = _squared_l2(point, c)
        if d < best_d:
            best_d = d
            best_j = j
    return best_j, best_d


def _kmeans_plus_plus_init(points: List[List[float]], k: int, rng: random.Random) -> List[List[float]]:
    n = len(points)
    if n == 0:
        return []
    if k <= 0:
        k = 1
    if k >= n:
        return [p[:] for p in points]

    first_idx = rng.randrange(n)
    centers = [points[first_idx][:]]
    used = {first_idx}
    min_dists = [_squared_l2(p, centers[0]) for p in points]

    while len(centers) < k:
        total = sum(min_dists)
        if total <= 1e-12:
            candidates = [i for i in range(n) if i not in used]
            if not candidates:
                break
            idx = rng.choice(candidates)
        else:
            r = rng.random() * total
            acc = 0.0
            idx = 0
            for i, d in enumerate(min_dists):
                acc += d
                if acc >= r:
                    idx = i
                    break
            if idx in used:
                candidates = [i for i in range(n) if i not in used]
                if candidates:
                    idx = rng.choice(candidates)
        used.add(idx)
        centers.append(points[idx][:])

        c_new = centers[-1]
        for i, p in enumerate(points):
            d = _squared_l2(p, c_new)
            if d < min_dists[i]:
                min_dists[i] = d

    return centers


def _kmeans_single_run(
    points: List[List[float]],
    *,
    k: int,
    max_iters: int,
    rng: random.Random,
) -> Tuple[List[int], List[List[float]], float]:
    n = len(points)
    if n == 0:
        return [], [], 0.0

    dim = len(points[0])
    k = max(1, min(k, n))
    centers = _kmeans_plus_plus_init(points, k, rng)
    if not centers:
        return [0] * n, [points[0][:]], 0.0
    k = len(centers)

    labels = [-1] * n
    inertia = float("inf")

    for _ in range(max(1, max_iters)):
        changed = False
        inertia = 0.0
        for i, p in enumerate(points):
            cid, d = _nearest_center_idx(p, centers)
            inertia += d
            if labels[i] != cid:
                labels[i] = cid
                changed = True

        counts = [0] * k
        new_centers = [[0.0] * dim for _ in range(k)]
        for i, p in enumerate(points):
            cid = labels[i]
            counts[cid] += 1
            acc = new_centers[cid]
            for d in range(dim):
                acc[d] += p[d]

        for cid in range(k):
            if counts[cid] <= 0:
                ridx = rng.randrange(n)
                new_centers[cid] = points[ridx][:]
                continue
            inv = 1.0 / float(counts[cid])
            for d in range(dim):
                new_centers[cid][d] *= inv

        centers = new_centers
        if not changed:
            break

    return labels, centers, float(inertia)


def run_kmeans_backend(
    vectors_cluster: List[List[float]],
    *,
    min_cluster_size: int,
    max_clusters: int,
    random_state: int,
    n_init: int,
    max_iter: int,
) -> Tuple[List[int], Dict[str, Any]]:
    n = len(vectors_cluster)
    if n == 0:
        return [], {"initial_clusters": 0}

    min_cluster_size = max(1, int(min_cluster_size or 1))
    max_clusters = max(1, int(max_clusters or 1))

    if n < min_cluster_size:
        return [0] * n, {"initial_clusters": 1}

    k0 = min(max_clusters, max(1, n // min_cluster_size))
    if k0 > n:
        k0 = n
    if k0 <= 1:
        return [0] * n, {"initial_clusters": 1}

    best_labels: List[int] | None = None
    best_inertia = float("inf")
    n_init = max(1, int(n_init or 1))
    max_iter = max(1, int(max_iter or 1))
    seed = int(random_state)

    for run_idx in range(n_init):
        rng = random.Random(seed + run_idx)
        labels, _, inertia = _kmeans_single_run(
            vectors_cluster,
            k=k0,
            max_iters=max_iter,
            rng=rng,
        )
        if best_labels is None or inertia < best_inertia:
            best_labels = labels[:]
            best_inertia = inertia

    return best_labels or ([0] * n), {"initial_clusters": int(k0)}


def _hierarchical_scipy_labels(
    vectors_cluster: List[List[float]],
    *,
    linkage: str,
    distance_threshold: float,
) -> List[int] | None:
    if _np is None:
        return None
    try:
        from scipy.cluster.hierarchy import fcluster, linkage as scipy_linkage  # type: ignore
        from scipy.spatial.distance import pdist  # type: ignore
    except Exception:
        return None

    try:
        X = _np.asarray(vectors_cluster, dtype=_np.float64)
        if X.ndim != 2 or X.shape[0] == 0:
            return []
        if X.shape[0] == 1:
            return [0]

        dist_vec = pdist(X, metric="cosine")
        if _np.isnan(dist_vec).any():
            dist_vec = _np.nan_to_num(dist_vec, nan=1.0, posinf=1.0, neginf=1.0)

        Z = scipy_linkage(dist_vec, method=linkage)
        raw = fcluster(Z, t=float(distance_threshold), criterion="distance")
        mapping: Dict[int, int] = {}
        next_id = 0
        labels: List[int] = []
        for x in raw.tolist():
            xi = int(x)
            if xi not in mapping:
                mapping[xi] = next_id
                next_id += 1
            labels.append(mapping[xi])
        return labels
    except Exception:
        return None


def _pairwise_cosine_distance_matrix(points: List[List[float]]) -> List[List[float]]:
    n = len(points)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        pi = points[i]
        for j in range(i + 1, n):
            d = _cosine_distance(pi, points[j])
            mat[i][j] = d
            mat[j][i] = d
    return mat


def _cluster_distance(
    a: List[int],
    b: List[int],
    *,
    pairwise_dist: List[List[float]],
    linkage: str,
) -> float:
    if linkage == "complete":
        m = 0.0
        for i in a:
            row = pairwise_dist[i]
            for j in b:
                d = row[j]
                if d > m:
                    m = d
        return m

    s = 0.0
    cnt = 0
    for i in a:
        row = pairwise_dist[i]
        for j in b:
            s += row[j]
            cnt += 1
    if cnt <= 0:
        return 1.0
    return s / float(cnt)


def _hierarchical_fallback_labels(
    vectors_cluster: List[List[float]],
    *,
    linkage: str,
    distance_threshold: float,
) -> List[int]:
    n = len(vectors_cluster)
    if n == 0:
        return []
    if n == 1:
        return [0]
    if distance_threshold <= 0.0:
        return list(range(n))

    pairwise = _pairwise_cosine_distance_matrix(vectors_cluster)
    clusters: Dict[int, List[int]] = {i: [i] for i in range(n)}
    next_id = n

    while len(clusters) > 1:
        cids = sorted(clusters.keys())
        best_pair: Tuple[int, int] | None = None
        best_dist = float("inf")

        for i in range(len(cids)):
            ci = cids[i]
            for j in range(i + 1, len(cids)):
                cj = cids[j]
                d = _cluster_distance(
                    clusters[ci],
                    clusters[cj],
                    pairwise_dist=pairwise,
                    linkage=linkage,
                )
                if d < best_dist:
                    best_dist = d
                    best_pair = (ci, cj)

        if best_pair is None or best_dist > distance_threshold:
            break

        a, b = best_pair
        merged = clusters[a] + clusters[b]
        del clusters[a]
        del clusters[b]
        clusters[next_id] = merged
        next_id += 1

    labels = [0] * n
    for new_label, cid in enumerate(sorted(clusters.keys())):
        for idx in clusters[cid]:
            labels[idx] = new_label
    return labels


def run_hierarchical_backend(
    vectors_cluster: List[List[float]],
    *,
    distance_threshold: float,
    linkage: str,
) -> Tuple[List[int], Dict[str, Any]]:
    labels = _hierarchical_scipy_labels(
        vectors_cluster,
        linkage=linkage,
        distance_threshold=distance_threshold,
    )
    backend = "scipy"
    if labels is None:
        labels = _hierarchical_fallback_labels(
            vectors_cluster,
            linkage=linkage,
            distance_threshold=distance_threshold,
        )
        backend = "python_fallback"
    return labels, {"hierarchical_backend": backend, "initial_clusters": len(set(labels))}


def reassign_small_clusters(
    labels: List[int],
    *,
    vectors_1024: List[List[float]],
    min_cluster_size: int,
) -> Tuple[List[int], Dict[str, Any]]:
    if not labels:
        return labels, {"raw_clusters": 0, "small_clusters": 0, "clusters_after_small_reassign": 0}

    min_cluster_size = max(1, int(min_cluster_size or 1))
    members = _labels_to_members(labels)
    raw_clusters = len(members)
    large = {cid: idxs for cid, idxs in members.items() if len(idxs) >= min_cluster_size}
    small_clusters = raw_clusters - len(large)

    if not large:
        return [0] * len(labels), {
            "raw_clusters": raw_clusters,
            "small_clusters": raw_clusters,
            "clusters_after_small_reassign": 1,
        }

    if small_clusters <= 0:
        return labels, {
            "raw_clusters": raw_clusters,
            "small_clusters": 0,
            "clusters_after_small_reassign": raw_clusters,
        }

    dim = len(vectors_1024[0]) if vectors_1024 else 0
    large_centroids: Dict[int, List[float]] = {}
    for cid, idxs in large.items():
        c = _mean_vector_from_indices(vectors_1024, idxs, dim)
        large_centroids[cid] = l2_normalize(c)

    new_labels = labels[:]
    for cid, idxs in members.items():
        if cid in large:
            continue
        for idx in idxs:
            v = vectors_1024[idx]
            best_cid = None
            best_dist = float("inf")
            for lid, lc in large_centroids.items():
                d = _cosine_distance(v, lc)
                if d < best_dist:
                    best_dist = d
                    best_cid = lid
            new_labels[idx] = int(best_cid if best_cid is not None else next(iter(large.keys())))

    final_members = _labels_to_members(new_labels)
    return new_labels, {
        "raw_clusters": raw_clusters,
        "small_clusters": small_clusters,
        "clusters_after_small_reassign": len(final_members),
    }


def merge_close_clusters_if_enabled(
    labels: List[int],
    *,
    vectors_1024: List[List[float]],
    enable_post_merge: bool,
    merge_distance_threshold: float,
) -> Tuple[List[int], Dict[str, Any]]:
    if not labels:
        return labels, {"post_merge_performed": False, "clusters_after_post_merge": 0}
    members = _labels_to_members(labels)
    if (not enable_post_merge) or merge_distance_threshold <= 0.0 or len(members) <= 1:
        return labels, {
            "post_merge_performed": False,
            "clusters_after_post_merge": len(members),
        }

    dim = len(vectors_1024[0]) if vectors_1024 else 0
    centroids: Dict[int, List[float]] = {}
    for cid, idxs in members.items():
        c = _mean_vector_from_indices(vectors_1024, idxs, dim)
        centroids[cid] = l2_normalize(c)

    cids = sorted(centroids.keys())
    parent: Dict[int, int] = {cid: cid for cid in cids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    for i in range(len(cids)):
        ci = cids[i]
        for j in range(i + 1, len(cids)):
            cj = cids[j]
            d = _cosine_distance(centroids[ci], centroids[cj])
            if d < merge_distance_threshold:
                union(ci, cj)

    merged_labels = [find(int(cid)) for cid in labels]
    return merged_labels, {
        "post_merge_performed": True,
        "clusters_after_post_merge": len(_labels_to_members(merged_labels)),
    }


def compute_final_centroids_1024(
    labels: List[int],
    *,
    vectors_1024: List[List[float]],
) -> Tuple[Dict[int, List[int]], Dict[int, List[float]], Dict[int, int]]:
    members = _labels_to_members(labels)
    dim = len(vectors_1024[0]) if vectors_1024 else 0
    centroids: Dict[int, List[float]] = {}
    sizes: Dict[int, int] = {}
    for cid, idxs in members.items():
        c = _mean_vector_from_indices(vectors_1024, idxs, dim)
        centroids[cid] = l2_normalize(c)
        sizes[cid] = len(idxs)
    return members, centroids, sizes


def renumber_clusters_stable(
    *,
    article_ids: List[int],
    labels: List[int],
    cluster_members: Dict[int, List[int]],
    cluster_centroids_1024: Dict[int, List[float]],
    cluster_sizes: Dict[int, int],
) -> Tuple[List[int], Dict[int, List[int]], Dict[int, List[float]], Dict[int, int]]:
    old_cids = list(cluster_members.keys())
    old_cids.sort(
        key=lambda cid: (
            -len(cluster_members[cid]),
            min(article_ids[idx] for idx in cluster_members[cid]) if cluster_members[cid] else 10**18,
        )
    )
    cid_map = {old: i + 1 for i, old in enumerate(old_cids)}

    new_labels = [cid_map[int(cid)] for cid in labels]
    new_members = {cid_map[c]: cluster_members[c] for c in old_cids}
    new_centroids = {cid_map[c]: cluster_centroids_1024[c] for c in old_cids}
    new_sizes = {cid_map[c]: cluster_sizes[c] for c in old_cids}
    return new_labels, new_members, new_centroids, new_sizes


def persist_interest_clusters(
    conn: sqlite3.Connection,
    *,
    article_ids: List[int],
    cluster_members: Dict[int, List[int]],
    cluster_centroids_1024: Dict[int, List[float]],
    cluster_sizes: Dict[int, int],
    algo: str,
    use_pca: bool,
    pca_variance_threshold: float,
    pca_dim: int | None,
    pca_explained_variance: float | None,
    tag_weight: float,
) -> None:
    _ensure_interest_tables(conn)
    conn.execute("DELETE FROM interest_cluster_members")
    conn.execute("DELETE FROM interest_clusters")

    dim = _resolve_vec_dim()
    now = now_iso_z()
    for cluster_id in sorted(cluster_members.keys()):
        size = int(cluster_sizes.get(cluster_id, len(cluster_members[cluster_id])))
        centroid = cluster_centroids_1024[cluster_id]
        centroid_blob = floats_to_f32_blob(centroid, dim=dim)
        conn.execute(
            "INSERT INTO interest_clusters(id, label, size, summary_centroid, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (cluster_id, f"cluster-{cluster_id}", size, centroid_blob, now, now),
        )
        for idx in cluster_members[cluster_id]:
            aid = int(article_ids[idx])
            conn.execute(
                "INSERT INTO interest_cluster_members(cluster_id, article_id, membership) VALUES(?, ?, ?)",
                (cluster_id, aid, 1.0),
            )

    _ensure_interest_meta(conn)
    _upsert_interest_meta(conn, "interest_clusters_last_built_at", now)
    _upsert_interest_meta(conn, "interest_cluster_algo", str(algo))
    _upsert_interest_meta(conn, "interest_cluster_use_pca", "1" if use_pca else "0")
    _upsert_interest_meta(
        conn,
        "interest_cluster_pca_variance_threshold",
        str(float(pca_variance_threshold)),
    )
    _upsert_interest_meta(conn, "interest_cluster_pca_dim", "" if pca_dim is None else str(int(pca_dim)))
    _upsert_interest_meta(
        conn,
        "interest_cluster_pca_explained_variance",
        "" if pca_explained_variance is None else str(float(pca_explained_variance)),
    )
    _upsert_interest_meta(conn, "interest_cluster_tag_weight", str(float(tag_weight)))
    conn.commit()


def build_interest_clusters_from_config(
    conn: sqlite3.Connection,
    config: InterestClusterConfig,
) -> Tuple[BuildInterestClustersResult, Dict[str, Any]]:
    cfg = _normalize_cfg(config)
    dim = _resolve_vec_dim()

    records = load_articles_for_clustering(conn, dim=dim)
    article_ids, vectors_1024, input_stats = build_interest_vectors(
        records,
        dim=dim,
        tag_weight=cfg.tag_weight,
    )

    if not vectors_1024:
        _clear_interest_tables(conn)
        _ensure_interest_meta(conn)
        _upsert_interest_meta(conn, "interest_clusters_last_built_at", now_iso_z())
        _upsert_interest_meta(conn, "interest_cluster_algo", cfg.cluster_algo)
        _upsert_interest_meta(conn, "interest_cluster_use_pca", "0")
        _upsert_interest_meta(conn, "interest_cluster_pca_dim", "")
        _upsert_interest_meta(conn, "interest_cluster_tag_weight", str(float(cfg.tag_weight)))
        conn.commit()
        empty = BuildInterestClustersResult(
            article_ids=[],
            labels=[],
            cluster_members={},
            cluster_centroids_1024={},
            cluster_sizes={},
            algo=cfg.cluster_algo,
            use_pca=False,
            pca_dim=None,
            pca_explained_variance=None,
        )
        stats: Dict[str, Any] = {
            "input": {
                "total_rows": len(records),
                **input_stats,
            },
            "pca": {
                "enabled": False,
                "note": "no_vectors",
                "original_dim": dim,
                "selected_dim": None,
                "threshold": cfg.pca_explained_variance_threshold,
                "explained_variance": None,
            },
            "cluster": {
                "algo": cfg.cluster_algo,
                "raw_clusters": 0,
                "small_clusters": 0,
                "clusters_after_small_reassign": 0,
                "post_merge_performed": False,
                "final_clusters": 0,
            },
        }
        return empty, stats

    pca = fit_pca_if_enabled(
        vectors_1024,
        use_pca=cfg.use_pca,
        explained_variance_threshold=cfg.pca_explained_variance_threshold,
    )
    vectors_cluster = pca.vectors_cluster

    if cfg.cluster_algo == "hierarchical":
        labels, backend_stats = run_hierarchical_backend(
            vectors_cluster,
            distance_threshold=cfg.hierarchical_distance_threshold,
            linkage=cfg.hierarchical_linkage,
        )
    else:
        labels, backend_stats = run_kmeans_backend(
            vectors_cluster,
            min_cluster_size=cfg.min_cluster_size,
            max_clusters=cfg.max_clusters,
            random_state=cfg.kmeans_random_state,
            n_init=cfg.kmeans_n_init,
            max_iter=cfg.kmeans_max_iter,
        )

    labels, small_stats = reassign_small_clusters(
        labels,
        vectors_1024=vectors_1024,
        min_cluster_size=cfg.min_cluster_size,
    )

    post_merge_stats = {"post_merge_performed": False, "clusters_after_post_merge": len(set(labels))}
    if cfg.cluster_algo == "kmeans++":
        labels, post_merge_stats = merge_close_clusters_if_enabled(
            labels,
            vectors_1024=vectors_1024,
            enable_post_merge=cfg.enable_post_merge,
            merge_distance_threshold=cfg.merge_distance_threshold,
        )

    cluster_members, cluster_centroids_1024, cluster_sizes = compute_final_centroids_1024(
        labels,
        vectors_1024=vectors_1024,
    )
    labels, cluster_members, cluster_centroids_1024, cluster_sizes = renumber_clusters_stable(
        article_ids=article_ids,
        labels=labels,
        cluster_members=cluster_members,
        cluster_centroids_1024=cluster_centroids_1024,
        cluster_sizes=cluster_sizes,
    )

    final = BuildInterestClustersResult(
        article_ids=article_ids,
        labels=labels,
        cluster_members=cluster_members,
        cluster_centroids_1024=cluster_centroids_1024,
        cluster_sizes=cluster_sizes,
        algo=cfg.cluster_algo,
        use_pca=bool(pca.used),
        pca_dim=pca.dim,
        pca_explained_variance=pca.explained_variance,
    )

    persist_interest_clusters(
        conn,
        article_ids=final.article_ids,
        cluster_members=final.cluster_members,
        cluster_centroids_1024=final.cluster_centroids_1024,
        cluster_sizes=final.cluster_sizes,
        algo=final.algo,
        use_pca=final.use_pca,
        pca_variance_threshold=cfg.pca_explained_variance_threshold,
        pca_dim=final.pca_dim,
        pca_explained_variance=final.pca_explained_variance,
        tag_weight=cfg.tag_weight,
    )

    stats = {
        "input": {
            "total_rows": len(records),
            **input_stats,
        },
        "pca": {
            "enabled": bool(pca.used),
            "note": pca.note,
            "original_dim": dim,
            "selected_dim": pca.dim,
            "threshold": cfg.pca_explained_variance_threshold,
            "explained_variance": pca.explained_variance,
        },
        "cluster": {
            "algo": cfg.cluster_algo,
            **backend_stats,
            **small_stats,
            **post_merge_stats,
            "final_clusters": len(final.cluster_sizes),
            **_size_stats(final.cluster_sizes),
        },
    }
    return final, stats


def build_interest_clusters(
    conn: sqlite3.Connection,
    *,
    min_size: int,
    max_clusters: int,
    cluster_algo: str = "kmeans++",
    tag_weight: float = 0.75,
    use_pca: bool = True,
    pca_explained_variance_threshold: float = 0.95,
    kmeans_random_state: int = 42,
    kmeans_n_init: int = 10,
    kmeans_max_iter: int = 300,
    enable_post_merge: bool = True,
    merge_distance_threshold: float = 0.06,
    hierarchical_distance_threshold: float = 0.20,
    hierarchical_linkage: str = "average",
    **_: Any,
) -> Dict[str, Any]:
    cfg = InterestClusterConfig(
        min_cluster_size=min_size,
        max_clusters=max_clusters,
        tag_weight=tag_weight,
        cluster_algo=cluster_algo,
        use_pca=use_pca,
        pca_explained_variance_threshold=pca_explained_variance_threshold,
        kmeans_random_state=kmeans_random_state,
        kmeans_n_init=kmeans_n_init,
        kmeans_max_iter=kmeans_max_iter,
        enable_post_merge=enable_post_merge,
        merge_distance_threshold=merge_distance_threshold,
        hierarchical_distance_threshold=hierarchical_distance_threshold,
        hierarchical_linkage=hierarchical_linkage,
    )
    final, stats = build_interest_clusters_from_config(conn, cfg)
    return {
        "ok": True,
        "algo": final.algo,
        "use_pca": final.use_pca,
        "pca_dim": final.pca_dim,
        "pca_explained_variance": final.pca_explained_variance,
        "clusters": len(final.cluster_sizes),
        "articles": len(final.article_ids),
        "stats": stats,
    }
