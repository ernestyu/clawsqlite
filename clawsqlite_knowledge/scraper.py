# -*- coding: utf-8 -*-
"""
URL scraper integration.

clawsqlite_knowledge itself does not implement web scraping. It integrates with an external scraper.

You can configure the scraper command via:
- CLI flag: --scrape-cmd
- Env: CLAWSQLITE_SCRAPE_CMD (preferred) or legacy CLAWKB_SCRAPE_CMD

The command must accept the URL as its last argument and print UTF-8 text.
Recommended output format:
- It should include a line: "Title: <title>"
- The rest of the output is treated as markdown body.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional, Tuple

def scrape_url(url: str, *, scrape_cmd: Optional[str] = None, timeout: int = 120) -> Tuple[Optional[str], str]:
    cmd = scrape_cmd or os.environ.get("CLAWSQLITE_SCRAPE_CMD") or os.environ.get("CLAWKB_SCRAPE_CMD")
    if not cmd:
        raise RuntimeError("URL ingest requires a scraper. Set --scrape-cmd or env CLAWSQLITE_SCRAPE_CMD/CLAWKB_SCRAPE_CMD.")

    # Build argv safely. We avoid shell=True by default to reduce quoting issues.
    if "{url}" in cmd:
        formatted = cmd.format(url=url)
        argv = shlex.split(formatted)
    else:
        argv = shlex.split(cmd) + [url]

    p = subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Scraper failed (code={p.returncode}): {p.stderr.strip()[:500]}")

    out = p.stdout
    title: Optional[str] = None

    # Prefer new-style output with explicit METADATA / MARKDOWN sections.
    lines = out.splitlines()
    meta_idx = None
    md_idx = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "--- METADATA ---" and meta_idx is None:
            meta_idx = i
        elif s == "--- MARKDOWN ---" and md_idx is None:
            md_idx = i

    body: str
    if md_idx is not None:
        # New format: optional METADATA block, then MARKDOWN block.
        meta_lines = []
        if meta_idx is not None and meta_idx < md_idx:
            meta_lines = lines[meta_idx + 1 : md_idx]
        # Extract title from metadata if present.
        for ml in meta_lines:
            mls = ml.strip()
            if mls.lower().startswith("title:"):
                title = mls.split(":", 1)[1].strip()
                break
        body = "\n".join(lines[md_idx + 1 :]).strip()
    else:
        # Legacy format: optional "Title:" line followed by markdown body.
        body_lines = []
        for line in lines:
            if line.startswith("Title:"):
                title = line[len("Title:") :].strip()
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()

    if not body:
        body = out.strip()
    return title, body
