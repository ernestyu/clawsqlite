# clawkb

A local knowledge-base CLI for OpenClaw.

Default paths (override via flags or env):
- DB: /home/node/.openclaw/workspace/clawkb/clawkb.sqlite3
- Articles: /home/node/.openclaw/workspace/clawkb/articles/
- Bin: /home/node/.openclaw/workspace/clawkb/bin/clawkb (you can copy `bin/clawkb` there)

This project is designed to run in a container with:
- SQLite (python sqlite3)
- FTS5 enabled
- simple tokenizer extension: /usr/local/lib/libsimple.so (tokenizer name: simple)
- sqlite-vec extension vec0 (dimension: 1536)

Embedding env (global):
- EMBEDDING_MODEL
- EMBEDDING_BASE_URL
- EMBEDDING_API_KEY

Small LLM env (global):
- SMALL_LLM_MODEL
- SMALL_LLM_BASE_URL
- SMALL_LLM_API_KEY

Run:
- python -m clawkb --help
- bin/clawkb --help
