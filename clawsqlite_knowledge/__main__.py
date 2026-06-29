from .cli import main
from .utils import load_project_env

if __name__ == '__main__':  # pragma: no cover
    # Load project-level .env before parsing CLI args for optional tuning knobs.
    # Knowledge runtime config itself should live in clawsqlite.toml.
    load_project_env()
    raise SystemExit(main())
