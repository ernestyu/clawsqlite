# -*- coding: utf-8 -*-
"""Interest cluster builder for clawsqlite_knowledge.

This module implements a simple k-means style clustering over article
embeddings stored in `articles_vec` / `articles_tag_vec` and writes the
result into `interest_clusters` and `interest_cluster_members` tables.

Design goals (v0):
- Keep dependencies minimal (pure Python + stdlib; no numpy/sklearn).
- Work on moderately sized KBs (hundreds to a few thousands of articles).
- Be deterministic enough for debugging (fixed init strategy).

The clusters are meant to be **optional metadata** used by higher level
applications (e.g. clawfeedradar) to interpret the KB as a set of
"interest topics".
"""
from __future__ import annotations

import math
import os
import sqlite3
from typing import Any, Dict, List, Tuple

from .utils import now_iso_z
from .embed import floats_to_f32_blob, _resolve_vec_dim


def _ensure_interest_tables(conn: sqlite3.Connection) -> None:
    """Create interest_* tables if they do not exist.

    Tables:
      - interest_clusters(id, label, size, summary_centroid, created_at, updated_at)
      - interest_cluster_members(cluster_id, article_id, membership)
    """
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


def _blob_to_floats(blob: bytes, dim: int) -> List[float]:
    import struct

    if len(blob) != 4 * dim:
        raise ValueError(f"Embedding blob size mismatch: got {len(blob)}, expected {4*dim}")
    return list(struct.unpack("<" + "f" * dim, blob))


def _squared_l2(a: List[float], b: List[float]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def _kmeans(points: List[List[float]], k: int, *, max_iters: int = 10) -> Tuple[List[int], List[List[float]]]:
    """Very small k-means implementation.

    Returns (assignments, centers):
      - assignments: len(points) ints in [0, k-1]
      - centers: k center vectors
    """
    n = len(points)
    if k <= 0:
        k = 1
    if n == 0:
        return [], []
    if n <= k:
        # Degenerate: one point per cluster up to k.
        centers = [p[:] for p in points]
        assignments = list(range(n))
        return assignments, centers

    dim = len(points[0])

    # Deterministic init: pick k points roughly evenly along the dataset.
    centers: List[List[float]] = []
    step = max(1, n // k)
    idx = 0
    used = set()
    for _ in range(k):
        if idx >= n:
            idx = n - 1
        if idx in used:
            idx = (idx + 1) % n
        centers.append(points[idx][:])
        used.add(idx)
        idx += step

    assignments = [0] * n

    for _ in range(max_iters):
        changed = False
        # Assign step
        for i, p in enumerate(points):
            best_j = 0
            best_d = float("inf")
            for j, c in enumerate(centers):
                d = _squared_l2(p, c)
                if d < best_d:
                    best_d = d
                    best_j = j
            if assignments[i] != best_j:
                assignments[i] = best_j
                changed = True

        # Update step
        counts = [0] * k
        new_centers = [[0.0] * dim for _ in range(k)]
        for i, p in enumerate(points):
            j = assignments[i]
            counts[j] += 1
            cj = new_centers[j]
            for d in range(dim):
                cj[d] += p[d]
        for j in range(k):
            if counts[j] > 0:
                inv = 1.0 / counts[j]
                cj = new_centers[j]
                for d in range(dim):
                    cj[d] *= inv
            else:
                # If a cluster lost all points, keep previous center.
                new_centers[j] = centers[j][:]
        centers = new_centers

        if not changed:
            break

    return assignments, centers


def build_interest_clusters(
    conn: sqlite3.Connection,
    *,
    min_size: int,
    max_clusters: int,
) -> Dict[str, Any]:
    """Build interest clusters from existing article embeddings.

    This implementation builds interest vectors from a mix of summary and
    tag embeddings when available, and considers undeleted articles with
    non-empty summaries.
    """
    # Load candidate vectors: summary + optional tag embeddings.
    dim = _resolve_vec_dim()
    rows = conn.execute(
        """
SELECT a.id AS id,
       sv.embedding AS summary_embedding,
       tv.embedding AS tag_embedding
FROM articles a
LEFT JOIN articles_vec sv ON sv.id = a.id
LEFT JOIN articles_tag_vec tv ON tv.id = a.id
WHERE a.deleted_at IS NULL
  AND a.summary IS NOT NULL AND trim(a.summary) != ''
"""
    ).fetchall()

    ids: List[int] = []
    points: List[List[float]] = []
    # Weights for mixing summary/tag embeddings into an interest vector.
    w_tag = float(os.environ.get("CLAWSQLITE_INTEREST_TAG_WEIGHT", "0.75") or 0.75)
    if w_tag < 0.0:
        w_tag = 0.0
    if w_tag > 1.0:
        w_tag = 1.0
    w_sum = 1.0 - w_tag

    for r in rows:
        try:
            sv_blob = r["summary_embedding"]
            tv_blob = r["tag_embedding"]
            if sv_blob is None and tv_blob is None:
                continue
            # Decode available embeddings.
            sv = _blob_to_floats(sv_blob, dim) if sv_blob is not None else None
            tv = _blob_to_floats(tv_blob, dim) if tv_blob is not None else None
            # Fallback logic:
            # - If both are present: mix with weights.
            # - If only one is present: use it as-is.
            if sv is None and tv is not None:
                vec = tv
            elif tv is None and sv is not None:
                vec = sv
            else:
                # Both present: build interest vector as weighted sum.
                vec = [0.0] * dim
                if w_sum > 0.0:
                    for i in range(dim):
                        vec[i] += w_sum * sv[i]
                if w_tag > 0.0:
                    for i in range(dim):
                        vec[i] += w_tag * tv[i]
            ids.append(int(r["id"]))
            points.append(vec)
        except Exception:
            continue

    n = len(points)
    if n == 0:
        # Nothing to cluster; clear existing clusters and return.
        _ensure_interest_tables(conn)
        conn.execute("DELETE FROM interest_cluster_members")
        conn.execute("DELETE FROM interest_clusters")
        conn.commit()
        return {"ok": True, "clusters": 0, "articles": 0}

    # Determine number of clusters based on min_size/max_clusters.
    min_size = max(1, int(min_size) or 1)
    max_clusters = max(1, int(max_clusters) or 1)

    if n <= min_size:
        k = 1
    else:
        max_by_size = max(1, n // min_size)
        k = min(max_clusters, max_by_size)
        if k <= 0:
            k = 1

    assignments, centers = _kmeans(points, k)
    if not assignments:
        # Safety fallback
        _ensure_interest_tables(conn)
        conn.execute("DELETE FROM interest_cluster_members")
        conn.execute("DELETE FROM interest_clusters")
        conn.commit()
        return {"ok": True, "clusters": 0, "articles": 0}

    # Build initial cluster membership
    cluster_members: Dict[int, List[int]] = {j: [] for j in range(len(centers))}
    for idx, cid in enumerate(assignments):
        cluster_members[cid].append(idx)

    # Apply min_size: keep only clusters with enough members; others will be
    # reassigned to the nearest large cluster.
    large_clusters = [j for j, members in cluster_members.items() if len(members) >= min_size]
    if not large_clusters:
        # All clusters are tiny; fallback to a single cluster containing all points.
        large_clusters = [0]
        cluster_members = {0: list(range(n))}
        centers = [centers[0]]

    # Reassign small-cluster points to nearest large center.
    if len(large_clusters) < len(centers):
        large_centers = {j: centers[j] for j in large_clusters}
        new_members: Dict[int, List[int]] = {j: [] for j in large_clusters}
        for j, members in cluster_members.items():
            if j in large_clusters:
                new_members[j].extend(members)
            else:
                for idx in members:
                    p = points[idx]
                    best_j = None
                    best_d = float("inf")
                    for lj, c in large_centers.items():
                        d = _squared_l2(p, c)
                        if d < best_d:
                            best_d = d
                            best_j = lj
                    if best_j is None:
                        best_j = large_clusters[0]
                    new_members[best_j].append(idx)
        cluster_members = new_members
        centers = [large_centers[j] for j in large_clusters]

    # Recompute centers based on final memberships
    final_clusters: Dict[int, List[int]] = {}
    final_centers: Dict[int, List[float]] = {}
    dim = len(points[0])
    for cid, members in cluster_members.items():
        if not members:
            continue
        acc = [0.0] * dim
        for idx in members:
            p = points[idx]
            for d in range(dim):
                acc[d] += p[d]
        inv = 1.0 / len(members)
        for d in range(dim):
            acc[d] *= inv
        final_clusters[cid] = members
        final_centers[cid] = acc

    if not final_clusters:
        _ensure_interest_tables(conn)
        conn.execute("DELETE FROM interest_cluster_members")
        conn.execute("DELETE FROM interest_clusters")
        conn.commit()
        return {"ok": True, "clusters": 0, "articles": 0}

    # Optional post-merge step: merge clusters whose centroids are too close.
    merge_thresh = float(os.environ.get("CLAWSQLITE_INTEREST_MERGE_DISTANCE", "0.06") or 0.06)
    if merge_thresh > 0.0 and len(final_centers) > 1:
        cids = sorted(final_centers.keys())
        # Union-find structure over cluster ids
        parent: Dict[int, int] = {cid: cid for cid in cids}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            # Simple union: attach smaller id to larger or vice versa
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

        # Compute cosine distances between centroids and merge when below threshold.
        for i in range(len(cids)):
            ci = cids[i]
            vi = final_centers[ci]
            for j in range(i + 1, len(cids)):
                cj = cids[j]
                vj = final_centers[cj]
                # cosine distance = 1 - cos_sim
                na = math.sqrt(_squared_l2(vi, [0.0] * dim))
                nb = math.sqrt(_squared_l2(vj, [0.0] * dim))
                if na == 0.0 or nb == 0.0:
                    continue
                dot = sum(vi[d] * vj[d] for d in range(dim))
                cos_sim = dot / (na * nb)
                cos_dist = 1.0 - cos_sim
                if cos_dist < merge_thresh:
                    union(ci, cj)

        # Group clusters by representative
        groups: Dict[int, List[int]] = {}
        for cid in cids:
            root = find(cid)
            groups.setdefault(root, []).append(cid)

        # Build merged clusters
        merged_clusters: Dict[int, List[int]] = {}
        merged_centers: Dict[int, List[float]] = {}
        for root, members_cids in groups.items():
            merged_members: List[int] = []
            acc = [0.0] * dim
            count = 0
            for cid in members_cids:
                for idx in final_clusters[cid]:
                    merged_members.append(idx)
                    p = points[idx]
                    for d in range(dim):
                        acc[d] += p[d]
                    count += 1
            if count == 0:
                continue
            inv = 1.0 / count
            for d in range(dim):
                acc[d] *= inv
            merged_clusters[root] = merged_members
            merged_centers[root] = acc

        final_clusters = merged_clusters
        final_centers = merged_centers

    # Map internal cluster ids to sequential ids starting from 1.
    sorted_cids = sorted(final_clusters.keys())
    cid_map = {old: i + 1 for i, old in enumerate(sorted_cids)}

    _ensure_interest_tables(conn)

    # Clear old clusters.
    conn.execute("DELETE FROM interest_cluster_members")
    conn.execute("DELETE FROM interest_clusters")

    now = now_iso_z()
    total_clusters = len(sorted_cids)

    # Insert clusters and members.
    for old_cid in sorted_cids:
        cluster_id = cid_map[old_cid]
        members = final_clusters[old_cid]
        size = len(members)
        center_vec = final_centers[old_cid]
        centroid_blob = floats_to_f32_blob(center_vec, dim=dim)
        label = f"cluster-{cluster_id}"
        conn.execute(
            "INSERT INTO interest_clusters(id, label, size, summary_centroid, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (cluster_id, label, size, centroid_blob, now, now),
        )
        # Membership is a placeholder for now (1.0); we can refine later.
        for idx in members:
            aid = ids[idx]
            conn.execute(
                "INSERT INTO interest_cluster_members(cluster_id, article_id, membership) VALUES(?, ?, ?)",
                (cluster_id, aid, 1.0),
            )

    conn.commit()

    return {"ok": True, "clusters": total_clusters, "articles": n}
