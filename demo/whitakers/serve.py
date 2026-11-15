"""The offline demo with a second family: the Maos plus the family in a private data folder, in replay mode.

From the repo root:

    uv run --project api python -m demo.whitakers.serve                       # http://localhost:8000
    uv run --project api python -m demo.whitakers.serve --port 8030 --data-dir ~/stitch-private/demo-family/data
    uv run --project api python -m demo.whitakers.serve --check               # load and compile, exit
    uv run --project api python -m demo.whitakers.serve --channels            # also Stitch Channels

It is `python -m demo.replay.serve` (in-memory database, scripted runs, prepared stories)
with the family from --data-dir (default $STITCH_WHITAKERS_DIR, else
~/stitch-private/demo-family/data) seeded beside the Maos. Prepared stories come from
--replay-dir, else $STITCH_REPLAY_DIR, else <data-dir>/replays when it exists, else
~/stitch-private/replays; each is checked against the family that has its cast (or its
`familyId`), and replays in either family.

--channels mounts Stitch Channels the way api/seed/channels_demo.py runs it (STITCH_CHANNELS=1,
STITCH_CHANNELS_WORKER=1, STITCH_CHANNELS_TRANSCRIPTION=0 unless set, and its
CHANNELS_SHARED_SECRET rule), through api/main.py's own mounting, with the worker on the
replay manager. See demo/whitakers/README.md.

Exit codes: 0 done, 1 the port cannot be used, 2 bad family data or a bad prepared story
(every problem is named on stderr).
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[2]
API_DIR = REPO_DIR / "api"
DEMO_DIR = REPO_DIR / "demo"
APP = "demo.whitakers.app:app"

EXIT_OK = 0
EXIT_PORT = 1
EXIT_BAD_DATA = 2
_TRUTHY = frozenset({"1", "true", "yes"})


def _paths() -> None:
    for path in (API_DIR, REPO_DIR):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m demo.whitakers.serve",
        description="Run the offline demo API with the Maos and a second family from a data folder, "
        "in replay mode. See demo/whitakers/README.md.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default %(default)s)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default %(default)s)")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (data resets each time)")
    parser.add_argument("--data-dir", help="the family's data folder (default: $STITCH_WHITAKERS_DIR, "
                        "else ~/stitch-private/demo-family/data)")
    parser.add_argument("--replay-dir", help="prepared stories (default: $STITCH_REPLAY_DIR, else "
                        "<data-dir>/replays when it exists, else ~/stitch-private/replays)")
    parser.add_argument("--channels", action="store_true", help="mount Stitch Channels with the worker on replay runs")
    parser.add_argument("--check", action="store_true", help="load and compile the family and the stories, then exit")
    return parser


def environment(args: argparse.Namespace, environ: dict[str, str]) -> dict[str, str]:
    """The variables the launcher sets (pure). Raises ValueError for a bad Channels secret."""
    from demo.whitakers.data import DATA_DIR_ENV, data_dir

    env: dict[str, str] = {"STITCH_AGENT": "fake"}
    directory = Path(args.data_dir).expanduser().resolve() if args.data_dir else data_dir(environ)
    env[DATA_DIR_ENV] = str(directory)
    if args.replay_dir:
        env["STITCH_REPLAY_DIR"] = str(Path(args.replay_dir).expanduser().resolve())
    elif not (environ.get("STITCH_REPLAY_DIR") or "").strip() and (directory / "replays").is_dir():
        env["STITCH_REPLAY_DIR"] = str(directory / "replays")
    if args.channels:
        from seed.channels_demo import _secret  # its exact rule: >= 32 UTF-8 bytes, no edge whitespace

        _secret()
        env.update(STITCH_CHANNELS="1", STITCH_CHANNELS_WORKER="1")
        env["STITCH_CHANNELS_TRANSCRIPTION"] = environ.get("STITCH_CHANNELS_TRANSCRIPTION") or "0"
    else:
        env["STITCH_CHANNELS"] = "0"
    return env


def check_all(*, channels: bool = False) -> tuple[Any, list, Path, list[str]]:
    """Load and compile the family, seed both families into a throwaway in-memory database,
    and compile every prepared story against its family (`story_family`). With `channels`,
    also check every contact's enrollment preferences. Returns (family data, stories, replay
    directory, warnings). Raises WhitakerDataError naming every problem, or
    ReplayFixtureError / FileNotFoundError for a replay directory that cannot be loaded."""
    import mongomock
    from pydantic import ValidationError

    from db import client as db_client
    from seed.demo import seed_demo

    from demo.replay import manager as replay_manager
    from demo.replay.fixtures import ReplayFixtureError, validate_story
    from demo.whitakers.data import WhitakerDataError, data_dir, load_family
    from demo.whitakers.seed import seed_family

    try:
        data = load_family(data_dir(os.environ))
    except WhitakerDataError as exc:
        # Still name what is wrong with the prepared stories' files; they compile once the family loads.
        extra = ["replays: not compiled until the family loads"]
        try:
            replay_manager.configured_stories()
        except (ReplayFixtureError, FileNotFoundError) as replay_exc:
            extra.insert(0, f"replays: {replay_exc}")
        raise WhitakerDataError(exc.directory, [*exc.errors, *extra]) from exc
    replay_manager.add_replay_family(data.family_id)
    stories, directory = replay_manager.configured_stories()
    errors: list[str] = []
    warnings = list(data.warnings)
    if channels:
        from channels.settings import EnrollmentRequest

        for i, contact in enumerate(data.contacts):
            try:
                EnrollmentRequest(provider="imessage", handle=contact.handle, timezone=contact.timezone,
                                  cadenceDays=contact.cadence_days, quietHours=contact.quiet_hours)
            except ValidationError as exc:
                fields = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}))
                errors.append(f"family.json contacts[{i}] ({contact.person_id}): not a valid enrollment ({fields})")
    mongo = mongomock.MongoClient(tz_aware=True)
    try:
        db = mongo["stitch_whitakers_check"]
        db_client.ensure_indexes(db)
        seed_demo(db)
        try:
            seed_family(db, data)
        except Exception as exc:  # noqa: BLE001 - the database's own rules, named
            raise WhitakerDataError(data.directory, [f"seeding failed: {type(exc).__name__}: {exc}"]) from exc
        for story in stories:
            family_id = replay_manager.story_family(db, story)
            people, props, sets = replay_manager.family_inputs(db, family_id, story)
            if not people:
                warnings.append(f"replays/{story.name} is for family {family_id!r}, which is not seeded: not checked")
                continue
            try:
                validate_story(story, people=people, props=props, sets=sets)
            except ReplayFixtureError as exc:
                errors.append(f"replays/{story.name} (family {family_id}): {exc}")
    finally:
        mongo.close()
    if errors:
        raise WhitakerDataError(data.directory, errors)
    return data, stories, directory, warnings


def summary_lines(data: Any, stories: list, directory: Path) -> list[str]:
    """Counts and folder names only: never names, tellings, emails, phone numbers or passwords."""
    from demo.whitakers.data import summary

    lines = [
        f"  Family   {data.name} ({data.family_id}): {summary(data)}",
        "           sign in with the accounts in family.json",
    ]
    count = len(stories)
    lines.append(f"  Replay   {count} prepared {'story' if count == 1 else 'stories'} from {directory}")
    lines += [f"           {story.name}" for story in stories]
    lines.append("           any other telling gets the scripted hymn-book scene")
    return lines


def channels_lines(host: str, port: int) -> list[str]:
    return [
        "  Channels on: signed /internal/channels/* routes; the worker runs the scripted conversation",
        "           on replay runs, so texted and called-in tellings replay too",
        f"           bridge: CHANNELS_API_URL=http://{_display(host)}:{port} "
        "(replies to CHANNELS_BRIDGE_URL, default http://127.0.0.1:3011)",
    ]


def _display(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "::", "localhost") else host


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _paths()
    from seed import serve as seed_serve

    dotenv_on = (os.environ.get("STITCH_NO_DOTENV") or "").strip().lower() not in _TRUTHY
    dotenv = seed_serve.read_env_files() if dotenv_on else {}
    os.environ.update(seed_serve.demo_env(os.environ, dotenv_values=dotenv))
    try:
        os.environ.update(environment(args, dict(os.environ)))
    except ValueError as exc:
        print(f"whitakers: --channels: {exc}", file=sys.stderr)
        return EXIT_BAD_DATA

    importlib.import_module("main")  # loads the .env files, builds the app (reads STITCH_CHANNELS)
    from demo.replay.fixtures import ReplayFixtureError
    from demo.whitakers.data import WhitakerDataError

    try:
        data, stories, directory, warnings = check_all(channels=args.channels)
    except (WhitakerDataError, ReplayFixtureError, FileNotFoundError) as exc:
        print(f"whitakers: {exc}", file=sys.stderr)
        return EXIT_BAD_DATA
    for warning in warnings:
        print(f"whitakers: {warning}", file=sys.stderr)
    lines = summary_lines(data, stories, directory)
    if args.check:
        print("\n".join(lines))
        print("whitakers: the family and every prepared story compile")
        return EXIT_OK

    if not seed_serve.port_free(args.host, args.port):
        print(f"cannot listen on {args.host}:{args.port}: is the API already running? pass --port", file=sys.stderr)
        return EXIT_PORT

    import uvicorn

    if args.channels:
        lines += channels_lines(args.host, args.port)
    banner = seed_serve.banner(args.host, args.port).rstrip("\n").split("\n")
    print("\n".join([*banner[:-2], *lines, *banner[-2:], ""]), flush=True)
    uvicorn.run(APP, host=args.host, port=args.port, reload=args.reload,
                reload_dirs=[str(API_DIR), str(DEMO_DIR)] if args.reload else None)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
