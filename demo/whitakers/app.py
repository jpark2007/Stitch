"""api/main.py's app with the Whitakers seeded beside the Maos, in replay mode: `demo.whitakers.app:app`.

Importing this imports api/main.py (which loads the .env files first, as uvicorn would),
makes its fake-database startup also seed the family from STITCH_WHITAKERS_DIR
(`seed_on_fake_db`), installs replay runs (demo/replay/app.py's wiring) and, when the app
was built with STITCH_CHANNELS=1, the Channels worker on those runs. Nothing in api/ is
edited. Started by `python -m demo.whitakers.serve`, which sets the environment first.
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
from demo.whitakers.wiring import seed_on_fake_db, wrap_channels  # noqa: E402

seed_on_fake_db()
use_replay_runs()
app = main.app
wrap_lifespan(app, main.fake_run_step_seconds)
wrap_channels(app)
