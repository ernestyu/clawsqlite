# -*- coding: utf-8 -*-
from __future__ import annotations

"""Interest report generator.

Generates a Markdown + optional PDF report summarising knowledge base
activity over a recent time window, with a focus on interest clusters.

The layout and language are intentionally simple to keep parsing and
future automation easy.
"""

import datetime as _dt
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .embed import _resolve_vec_dim
from .db import _find_vec0_so
from .inspect_interest import _cosine_distance, _load_clusters, _load_interest_vectors
from .utils import extract_keywords_light

try:  # optional plotting
    import matplotlib.pyplot as plt  # type: ignore
except Exception:  # pragma: no cover
    plt = None


@dataclass
class ArticleRow:
    id: int
    title: str
    tags: str
    summary: str
    created_at: str


_LANG_STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "clawsqlite knowledge report",
        "overview": "## 1. Overview",
        "daily": "## 2. Daily new articles",
        "clusters": "## 3. Distribution by interest cluster",
        "trend": "## 4. Heating / cooling interests",
        "window": "Window",
        "total_new": "New articles",
        "total_clusters": "Clusters touched",
        "col_date": "Date",
        "col_count": "New articles",
        "col_cluster_id": "Cluster ID",
        "col_theme": "Theme",
        "col_articles": "Articles",
        "col_share": "Share",
        "col_mean_radius": "mean_radius",
        "col_prev": "Prev window",
        "col_diff": "Delta",
        "daily_chart": "Daily new articles",
        "cluster_chart": "Articles per interest cluster",
        "pca_chart": "Interest cluster PCA (size=size, color=mean_radius)",
        "trend_table_title": "Heating / cooling clusters (current vs previous window)",
    },
    "zh": {
        "title": "clawsqlite 知识库周报",
        "overview": "## 1. 总览",
        "daily": "## 2. 每日新增文章数",
        "clusters": "## 3. 按兴趣簇分布",
        "trend": "## 4. 升温 / 降温兴趣",
        "window": "时间窗口",
        "total_new": "新增文章数",
        "total_clusters": "涉及兴趣簇数",
        "col_date": "日期",
        "col_count": "新增文章数",
        "col_cluster_id": "簇 ID",
        "col_theme": "主题概括",
        "col_articles": "文章数",
        "col_share": "占比",
        "col_mean_radius": "mean_radius",
        "col_prev": "上一窗口",
        "col_diff": "变化",
        "daily_chart": "每日新增文章数",
        "cluster_chart": "按兴趣簇分布",
        "pca_chart": "兴趣簇 PCA（size=size, color=mean_radius）",
        "trend_table_title": "升温 / 降温簇（当前窗口 vs 上一窗口）",
    },
}


def _resolve_lang(lang: Optional[str]) -> str:
    env_lang = os.environ.get("CLAWSQLITE_REPORT_LANG")
    value = (lang or env_lang or "en").strip().lower()
    if value.startswith("zh"):
        return "zh"
    return "en"


def _load_articles_in_window(
    conn: sqlite3.Connection,
    *,
    start: _dt.datetime,
    end: _dt.datetime,
) -> List[ArticleRow]:
    cur = conn.cursor()
    cur.execute(
        """
SELECT id, title, tags, summary, created_at
FROM articles
WHERE deleted_at IS NULL
  AND created_at >= ?
  AND created_at < ?
ORDER BY created_at ASC
        """,
        (start.isoformat(), end.isoformat()),
    )
    rows = cur.fetchall()
    out: List[ArticleRow] = []
    for rid, title, tags, summary, created_at in rows:
        out.append(
            ArticleRow(
                id=int(rid),
                title=title or "",
                tags=tags or "",
                summary=summary or "",
                created_at=created_at,
            )
        )
    return out


def _group_by_date(articles: List[ArticleRow]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for a in articles:
        d = (a.created_at or "")[:10]
        if not d:
            continue
        counts[d] = counts.get(d, 0) + 1
    return counts


def _cluster_counts_for_articles(
    conn: sqlite3.Connection,
    article_ids: List[int],
) -> Dict[int, int]:
    if not article_ids:
        return {}
    cur = conn.cursor()
    # Use a temporary table-like IN list; for typical report windows this is small.
    placeholders = ",".join(["?"] * len(article_ids))
    cur.execute(
        f"SELECT cluster_id, COUNT(*) FROM interest_cluster_members "
        f"WHERE article_id IN ({placeholders}) GROUP BY cluster_id",
        article_ids,
    )
    counts: Dict[int, int] = {}
    for cid, cnt in cur.fetchall():
        counts[int(cid)] = int(cnt)
    return counts


def _top_articles_for_cluster(
    articles: List[ArticleRow],
    memberships: Dict[int, List[int]],
    cluster_id: int,
    *,
    limit: int,
) -> List[ArticleRow]:
    ids = memberships.get(cluster_id) or []
    # 保持时间顺序：越新的越靠前
    id_set = set(ids)
    selected = [a for a in articles if a.id in id_set]
    return selected[-limit:][::-1]


def _load_cluster_memberships(
    conn: sqlite3.Connection,
    article_ids: List[int],
) -> Dict[int, List[int]]:
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(article_ids))
    cur.execute(
        f"SELECT cluster_id, article_id FROM interest_cluster_members "
        f"WHERE article_id IN ({placeholders})",
        article_ids,
    )
    by_cluster: Dict[int, List[int]] = {}
    for cid, aid in cur.fetchall():
        cid = int(cid)
        aid = int(aid)
        by_cluster.setdefault(cid, []).append(aid)
    return by_cluster


def _theme_for_cluster(
    cluster_id: int,
    articles: List[ArticleRow],
    memberships: Dict[int, List[int]],
    *,
    max_tokens: int = 5,
) -> str:
    ids = set(memberships.get(cluster_id) or [])
    if not ids:
        return ""

    # 只用时间窗中的文章
    tags_counter: Dict[str, int] = {}
    text_blob: List[str] = []
    for a in articles:
        if a.id not in ids:
            continue
        # tags
        if a.tags:
            for raw in a.tags.replace("，", ",").split(","):
                t = raw.strip()
                if not t:
                    continue
                tags_counter[t] = tags_counter.get(t, 0) + 1
        # title + summary
        text_blob.append(a.title or "")
        text_blob.append(a.summary or "")

    tokens: List[str] = []
    # 先取出现频最高的 tags
    if tags_counter:
        items = sorted(tags_counter.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0].lower()))
        for tag, _ in items:
            tokens.append(tag)
            if len(tokens) >= max_tokens:
                break

    # 再从 title+summary 中补 keywords
    if len(tokens) < max_tokens and text_blob:
        blob = "\n".join(text_blob)
        kw = extract_keywords_light(blob, max_k=max_tokens * 2)
        # 去掉已存在的 tag，避免重复
        seen = {t.lower() for t in tokens}
        for k in kw:
            k_norm = (k or "").strip()
            if not k_norm:
                continue
            low = k_norm.lower()
            if low in seen:
                continue
            tokens.append(k_norm)
            seen.add(low)
            if len(tokens) >= max_tokens:
                break

    return "/".join(tokens[:max_tokens]) if tokens else ""


def _ensure_mplconfigdir() -> None:
    """Ensure Matplotlib can write configs and has a CJK-capable font.

    - MPLCONFIGDIR is set to a writable location to avoid ~/.cache issues.
    - When possible, prefer Noto Sans CJK (fonts-noto-cjk) for Chinese text,
      falling back to DejaVu Sans.
    """
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
    try:
        import matplotlib

        # Prefer Noto Sans CJK if available (installed via fonts-noto-cjk),
        # then fall back to DejaVu Sans.
        font_candidates = [
            "Noto Sans CJK SC",
            "Noto Sans CJK JP",
            "Noto Sans CJK TC",
            "Noto Sans CJK KR",
            "Noto Sans CJK",
            "DejaVu Sans",
        ]
        matplotlib.rcParams["font.family"] = font_candidates
        matplotlib.rcParams["font.sans-serif"] = font_candidates
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        # Best-effort: if matplotlib is not available or rcParams cannot be
        # updated, we still proceed with default settings.
        pass


def run_interest_report(
    db_path: str,
    *,
    days: int = 7,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    vec_dim: Optional[int] = None,
    out_dir: str = "reports",
    lang: Optional[str] = None,
    no_pdf: bool = False,
) -> Path:
    """Generate an interest report and return the report directory path."""
    lang_code = _resolve_lang(lang)
    strings = _LANG_STRINGS.get(lang_code, _LANG_STRINGS["en"])

    db_path = os.path.abspath(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = None
        # Time window
        if date_from and date_to:
            start = _dt.datetime.fromisoformat(date_from)
            end = _dt.datetime.fromisoformat(date_to)
        else:
            today = _dt.date.today()
            end_date = today
            start_date = end_date - _dt.timedelta(days=days)
            start = _dt.datetime.combine(start_date, _dt.time.min)
            end = _dt.datetime.combine(end_date, _dt.time.min)

        # Current window and previous window
        prev_start = start - (end - start)
        prev_end = start

        # Load articles in both windows
        current_articles = _load_articles_in_window(conn, start=start, end=end)
        prev_articles = _load_articles_in_window(conn, start=prev_start, end=prev_end)

        current_ids = [a.id for a in current_articles]
        prev_ids = [a.id for a in prev_articles]

        # Daily counts (current window)
        daily_counts = _group_by_date(current_articles)

        # Cluster-level stats
        dim = vec_dim or _resolve_vec_dim()
        # Ensure vec0 for centroid inspection
        try:
            conn.enable_load_extension(True)
            ext = os.environ.get("CLAWSQLITE_VEC_EXT") or _find_vec0_so()
            if ext and ext.lower() != "none":
                conn.load_extension(ext)
        except Exception:
            pass

        clusters = _load_clusters(conn, vec_dim=dim)
        by_cluster_members_current = _load_cluster_memberships(conn, current_ids) if current_ids else {}
        by_cluster_members_prev = _load_cluster_memberships(conn, prev_ids) if prev_ids else {}

        # Per-cluster counts for current and previous windows.
        current_counts = _cluster_counts_for_articles(conn, current_ids)
        prev_counts = _cluster_counts_for_articles(conn, prev_ids)

        # mean_radius per cluster for current window
        mean_radius: Dict[int, float] = {}
        by_cluster_vecs = _load_interest_vectors(conn, dim=dim)
        for cid, cl in clusters.items():
            members = by_cluster_vecs.get(cid) or []
            if not members:
                continue
            dists = [_cosine_distance(cl.centroid, m.vec) for m in members]
            mean_radius[cid] = float(np.mean(dists))

        # Prepare report directory
        window_to_date = (end - _dt.timedelta(days=1)).date()
        report_root = Path(out_dir)
        report_dir = report_root / f"{window_to_date.strftime('%Y%m%d')}-report"
        images_dir = report_dir / "images"
        report_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)

        # 1) charts
        _ensure_mplconfigdir()
        if plt is not None and current_articles:
            # Daily chart
            dates_sorted = sorted(daily_counts.keys())
            counts_sorted = [daily_counts[d] for d in dates_sorted]
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(dates_sorted, counts_sorted)
            ax.set_title(strings["daily_chart"])
            ax.set_xlabel(strings["col_date"])
            ax.set_ylabel(strings["col_count"])
            fig.autofmt_xdate(rotation=45)
            fig.tight_layout()
            daily_path = images_dir / "daily_articles.png"
            fig.savefig(daily_path, dpi=150)
            plt.close(fig)

            # Cluster distribution chart (current window)
            cids_sorted = sorted(clusters.keys())
            x_labels = [str(cid) for cid in cids_sorted]
            y_counts = [current_counts.get(cid, 0) for cid in cids_sorted]
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(x_labels, y_counts)
            ax.set_title(strings["cluster_chart"])
            ax.set_xlabel(strings["col_cluster_id"])
            ax.set_ylabel(strings["col_articles"])
            fig.tight_layout()
            cluster_path = images_dir / "cluster_distribution.png"
            fig.savefig(cluster_path, dpi=150)
            plt.close(fig)

            # PCA chart of centroids
            if clusters:
                ids = sorted(clusters.keys())
                X = np.stack([clusters[cid].centroid for cid in ids])
                X_centered = X - X.mean(axis=0, keepdims=True)
                U, S, Vt = np.linalg.svd(X_centered, full_matrices=False)
                X2 = X_centered @ Vt[:2].T
                xs = X2[:, 0]
                ys = X2[:, 1]

                sizes = []
                mean_radii_plot = []
                for cid in ids:
                    cl = clusters[cid]
                    members = by_cluster_vecs.get(cid) or []
                    if members:
                        dists = [_cosine_distance(cl.centroid, m.vec) for m in members]
                        mrr = float(np.mean(dists))
                    else:
                        mrr = 0.0
                    sizes.append(cl.size)
                    mean_radii_plot.append(mrr)

                sizes_arr = np.array(sizes, dtype="float32")
                if sizes_arr.size > 0:
                    sizes_plot = 40.0 * np.sqrt(sizes_arr / sizes_arr.max())
                else:
                    sizes_plot = 40.0

                fig, ax = plt.subplots(figsize=(8, 6))
                sc = ax.scatter(xs, ys, s=sizes_plot, c=mean_radii_plot, cmap="viridis")
                for i, cid in enumerate(ids):
                    ax.text(xs[i], ys[i], str(cid), fontsize=8, ha="center", va="center")
                ax.set_title(strings["pca_chart"])
                ax.set_xlabel("PC1")
                ax.set_ylabel("PC2")
                cbar = fig.colorbar(sc, ax=ax, label="mean_radius (1 - cos)")
                cbar.ax.set_ylabel("mean_radius (1 - cos)", rotation=90, labelpad=10)
                fig.tight_layout()
                pca_path = images_dir / "interest_clusters_pca.png"
                fig.savefig(pca_path, dpi=150)
                plt.close(fig)

        # 2) build markdown
        md_lines: List[str] = []
        md_lines.append(f"# {strings['title']}")
        window_str = f"{start.date().isoformat()} - { (end - _dt.timedelta(days=1)).date().isoformat() }"
        md_lines.append("")
        md_lines.append(f"- {strings['window']}: {window_str}")
        md_lines.append(f"- {strings['total_new']}: {len(current_articles)}")
        touched_clusters = sum(1 for cid in clusters.keys() if current_counts.get(cid, 0) > 0)
        md_lines.append(f"- {strings['total_clusters']}: {touched_clusters}")
        md_lines.append("")

        # Section: daily
        md_lines.append(strings["daily"])
        md_lines.append("")
        if daily_counts:
            md_lines.append(f"| {strings['col_date']} | {strings['col_count']} |")
            md_lines.append("| --- | ---: |")
            for d in sorted(daily_counts.keys()):
                md_lines.append(f"| {d} | {daily_counts[d]} |")
            if plt is not None and current_articles:
                md_lines.append("")
                md_lines.append(f"![{strings['daily_chart']}](images/daily_articles.png)")
        else:
            md_lines.append("(no new articles in this window)")

        # Section: clusters
        md_lines.append("")
        md_lines.append(strings["clusters"])
        md_lines.append("")
        if current_counts:
            total = float(len(current_articles) or 1)
            md_lines.append(
                f"| {strings['col_cluster_id']} | {strings['col_theme']} | {strings['col_articles']} | {strings['col_share']} | {strings['col_mean_radius']} |"
            )
            md_lines.append("| --- | --- | ---: | ---: | ---: |")
            # For theme and examples we need memberships mapping
            memberships_current = by_cluster_members_current
            for cid in sorted(clusters.keys()):
                cnt = current_counts.get(cid, 0)
                if cnt == 0:
                    continue
                theme = _theme_for_cluster(cid, current_articles, memberships_current, max_tokens=5)
                share = cnt / total * 100.0
                mr = mean_radius.get(cid, 0.0)
                md_lines.append(
                    f"| {cid} | {theme or ''} | {cnt} | {share:.1f}% | {mr:.3f} |"
                )

            # cluster distribution chart
            if plt is not None and current_articles:
                md_lines.append("")
                md_lines.append(f"![{strings['cluster_chart']}](images/cluster_distribution.png)")

            # representative articles per cluster
            md_lines.append("")
            for cid in sorted(clusters.keys()):
                cnt = current_counts.get(cid, 0)
                if cnt == 0:
                    continue
                theme = _theme_for_cluster(cid, current_articles, memberships_current, max_tokens=5)
                md_lines.append("")
                md_lines.append(f"#### Cluster {cid} - {theme}")
                reps = _top_articles_for_cluster(
                    current_articles,
                    memberships_current,
                    cluster_id=cid,
                    limit=3,
                )
                if reps:
                    for a in reps:
                        date_str = (a.created_at or "")[:10]
                        title = a.title or "(untitled)"
                        md_lines.append(f"- [{date_str}] {title}")
                else:
                    md_lines.append("- (no articles in this window)")
        else:
            md_lines.append("(no cluster activity in this window)")

        # PCA chart reference
        if plt is not None and clusters:
            md_lines.append("")
            md_lines.append(f"![{strings['pca_chart']}](images/interest_clusters_pca.png)")

        # Section: trends
        md_lines.append("")
        md_lines.append(strings["trend"])
        md_lines.append("")
        if current_counts or prev_counts:
            md_lines.append(strings["trend_table_title"])
            md_lines.append("")
            md_lines.append(
                f"| {strings['col_cluster_id']} | {strings['col_theme']} | {strings['total_new']} | {strings['col_prev']} | {strings['col_diff']} |"
            )
            md_lines.append("| --- | --- | ---: | ---: | ---: |")
            memberships_prev = by_cluster_members_prev
            all_cids = sorted(set(list(current_counts.keys()) + list(prev_counts.keys())))
            for cid in all_cids:
                curr = current_counts.get(cid, 0)
                prev = prev_counts.get(cid, 0)
                if curr == 0 and prev == 0:
                    continue
                theme = _theme_for_cluster(cid, current_articles or prev_articles, memberships_current or memberships_prev, max_tokens=5)
                diff = curr - prev
                md_lines.append(f"| {cid} | {theme} | {curr} | {prev} | {diff:+d} |")
        else:
            md_lines.append("(no cluster activity in either window)")

        report_md_path = report_dir / "report.md"
        report_md_path.write_text("\n".join(md_lines), encoding="utf-8")

        # Optional PDF via pandoc
        if not no_pdf:
            try:
                import subprocess

                subprocess.run(
                    ["pandoc", str(report_md_path), "-o", str(report_dir / "report.pdf")],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                # Best-effort; PDF is a convenience, not required.
                pass

        return report_dir
    finally:
        conn.close()
