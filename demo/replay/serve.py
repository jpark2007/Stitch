"""The offline demo in replay mode: api/seed/serve.py, with prepared stories behind "add a memory".

From the repo root:

    uv run --project api python -m demo.replay.serve                    # http://localhost:8000
    uv run --project api python -m demo.replay.serve --port 8010         # another port
    STITCH_REPLAY_DIR=demo/replay/example uv run --project api python -m demo.replay.serve
    uv run --project api python -m demo.replay.serve --check             # compile the stories, exit

Same as `python -m seed.serve` (in-memory demo family, STITCH_FAKE_DB=1, the dev JWT secret
when none is configured, the port check, the banner, every other variable such as
STITCH_CORS_ORIGINS and STITCH_FAKE_RUN_STEP passed through), except that STITCH_AGENT is
always "fake" and the scripted runs are a ReplayRunManager (demo/replay/manager.py): a
telling that matches a prepared story ends on that story's scene, anything else on the
scripted hymn-book scene.

Before the server starts, every prepared story in STITCH_REPLAY_DIR (or --replay-dir;
default ~/stitch-private/replays) is loaded and compiled against the demo family. A story
that does not compile stops the launcher with exit code 2 and its name and errors on stderr.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
API_DIR = REPO_DIR / "api"
DEMO_DIR = REPO_DIR / "demo"
APP = "demo.replay.app:app"

EXIT_OK = 0
EXIT_PORT = 1
EXIT_BAD_STORY = 2
_TRUTHY = frozenset({"1", "true", "yes"})


def _paths() -> None:
    for path in (API_DIR, REPO_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m demo.replay.serve",
        description="Run the offline demo API (in-memory demo family, scripted runs) in replay mode: "
        "a telling that matches a prepared story ends on its prepared scene. See demo/replay/README.md.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default %(default)s)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default %(default)s)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (data resets each time)")
    parser.add_argument("--replay-dir", help="prepared stories directory (default: $STITCH_REPLAY_DIR, "
                        "else ~/stitch-private/replays)")
    parser.add_argument("--check", action="store_true", help="load and compile the prepared stories, then exit")
    return parser


def check_stories(stories: Sequence) -> list[str]:
    """Compile every story against the demo family, offline. Returns warnings; raises
    ReplayFixtureError for the first story that does not compile."""
    from db.props import library_props
    from seed import demo

    from demo.replay.fixtures import validate_story

    people = demo.scene_people()
    props = {**library_props(), **{p.id: p for p in demo.load_props()}}
    sets = {s.id: s for s in demo.load_sets()}
    warnings = []
    for story in stories:
        if story.family_id not in (None, demo.DEMO_FAMILY_ID):
            warnings.append(f"{story.name} is for family {story.family_id!r}, not the demo family: not checked")
            continue
        validate_story(story, people=people, props=props, sets=sets)
    return warnings


def replay_lines(stories: Sequence, directory: Path) -> list[str]:
    """The banner's replay lines: where the stories come from and their names (never their text)."""
    count = len(stories)
    lines = [f"  Replay   {count} prepared {'story' if count == 1 else 'stories'} from {directory}"]
    lines += [f"           {story.name}" for story in stories]
    lines.append("           any other telling gets the scripted hymn-book scene")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Start the demo API in replay mode. 1: the port cannot be used; 2: a bad prepared story."""
    args = _parser().parse_args(argv)
    _paths()
    from seed import serve as seed_serve

    dotenv_on = (os.environ.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY
    dotenv = seed_serve.read_env_files() if dotenv_on else {}
    os.environ.update(seed_serve.demo_env(os.environ, dotenv_values=dotenv))
    os.environ["STITCH_AGENT"] = "fake"
    if args.replay_dir:
        os.environ["STITCH_REPLAY_DIR"] = str(Path(args.replay_dir).expanduser().resolve())

    # main first, as uvicorn would: it loads the .env files before anything reads the environment.
    importlib.import_module("main")
    from demo.replay.fixtures import ReplayFixtureError
    from demo.replay.manager import configured_stories

    try:
        stories, directory = configured_stories()
        warnings = check_stories(stories)
    except (ReplayFixtureError, FileNotFoundError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return EXIT_BAD_STORY
    for warning in warnings:
        print(f"replay: {warning}", file=sys.stderr)
    if args.check:
        print("\n".join(replay_lines(stories, directory)))
        print(f"replay: {len(stories)} prepared {'story compiles' if len(stories) == 1 else 'stories compile'}")
        return EXIT_OK

    if not seed_serve.port_free(args.host, args.port):
        print(f"cannot listen on {args.host}:{args.port}: is the API already running? pass --port", file=sys.stderr)
        return EXIT_PORT

    import uvicorn

    banner = seed_serve.banner(args.host, args.port).rstrip("\n").split("\n")
    print("\n".join([*banner[:-2], *replay_lines(stories, directory), *banner[-2:], ""]), flush=True)
    uvicorn.run(
        APP,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(API_DIR), str(DEMO_DIR)] if args.reload else None,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
