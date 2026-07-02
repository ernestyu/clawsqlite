# clawsqlite-knowledge Skill

## What This Skill Is

`clawsqlite-knowledge` is a thin OpenClaw/ClawHub skill wrapper around the
published `clawsqlite` PyPI package.

It exists to help agents call the official `clawsqlite knowledge ...` CLI
consistently from a fixed component root.

## Relationship To clawsqlite

This skill does not implement knowledge-base logic. The implementation lives in
the upstream `clawsqlite` package and its official CLI.

This skill intentionally does not:

- vendor the `clawsqlite` source tree
- clone the GitHub repository
- add a `run_clawknowledge.py` runtime wrapper
- define a second JSON API
- maintain a second configuration system

## Installation

Install the skill shell into the workspace, then run:

```bash
sh bootstrap_deps.sh
```

The bootstrap script installs or upgrades `clawsqlite` from PyPI and performs a
minimal CLI check.

## Configuration

Create or edit the local private config in this skill directory:

```bash
clawsqlite knowledge maintenance init-config --out clawsqlite.toml
```

`clawsqlite.toml` is the single runtime configuration source. This skill does
not include an env example because normal operation should not use environment
variables as a second config layer.

## Validate With Doctor

```bash
clawsqlite knowledge maintenance doctor --json
```

## Common Commands

```bash
clawsqlite knowledge record ingest --url "https://example.com/post" --category web_article --json
clawsqlite knowledge record ingest --text "some note" --title "Saved note" --category note --json
clawsqlite knowledge record search "vector database design" --mode hybrid --json
clawsqlite knowledge record show --id 123 --full --json
clawsqlite knowledge maintenance cleanup --days 3 --dry-run --json
clawsqlite knowledge maintenance backup --dry-run --json
```

## When To Use clawsqlite Directly

Use `clawsqlite` directly whenever you are developing or debugging the upstream
package itself. Use this skill when an Agent needs a small, stable instruction
surface for operating a configured knowledge component.
