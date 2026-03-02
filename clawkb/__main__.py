from .cli import main
from .utils import load_project_env

if __name__ == '__main__':
    # Load project-level .env before parsing CLI args so that
    # embedding and root/DB settings can be supplied via ENV.example -> .env.
    load_project_env()
    raise SystemExit(main())
