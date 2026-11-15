"""api/main.py's app in replay mode, for uvicorn: `demo.replay.app:app`.

Importing this module imports api/main.py (which loads the .env files first, as uvicorn
would), then makes its scripted-run startup install a ReplayRunManager instead of the
scripted one (`use_replay_runs`), and checks right after startup that it did
(`ensure_replay_manager`). Nothing in api/ is edited. Started by `python -m demo.replay.serve`;
it needs the repo root and api/ on sys.path (serve.py sets both).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
API_DIR = REPO_DIR / "api"
for path in (API_DIR, REPO_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import main  # noqa: E402 - after sys.path, and before anything else reads the environment

from demo.replay.manager import use_replay_runs, wrap_lifespan  # noqa: E402

use_replay_runs()
app = main.app
wrap_lifespan(app, main.fake_run_step_seconds)
