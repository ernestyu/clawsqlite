# -*- coding: utf-8 -*-
"""
URL scraper integration.

clawkb itself does not implement web scraping. It integrates with an external scraper.

You can configure the scraper command via:
- CLI flag: --scrape-cmd
- Env: CLAWKB_SCRAPE_CMD

The command must accept the URL as its last argument and print UTF-8 text.
Recommended output format:
- It should include a line: "Title: <title>"
- The rest of the output is treated as markdown body.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple

def scrape_url(url: str, *, scrape_cmd: Optional[str] = None, timeout: int = 120) -> Tuple[Optional[str], str]:
    cmd = scrape_cmd or os.environ.get("CLAWKB_SCRAPE_CMD")
    if not cmd:
        raise RuntimeError("URL ingest requires a scraper. Set --scrape-cmd or env CLAWKB_SCRAPE_CMD.")
    # If cmd is a string, run via shell; to be safer, allow users to pass a template with {url}.
    if "{url}" in cmd:
        full_cmd = cmd.format(url=url)
        shell = True
        args = full_cmd
    else:
        # Split by whitespace minimally; users can still use {url} to avoid issues.
        shell = True
        args = f"{cmd} \"{url}\""

    p = subprocess.run(
        args,
        shell=shell,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(f"Scraper failed (code={p.returncode}): {p.stderr.strip()[:500]}")

    out = p.stdout
    title = None
    body_lines = []
    for line in out.splitlines():
        if line.startswith("Title:"):
            title = line[len("Title:"):].strip()
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if not body:
        body = out.strip()
    return title, body
