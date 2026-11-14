"""Run every flow-page browser script, one after another: the one command a teammate runs.

From api/ (it uses the API's uv environment, which has Playwright):

    cd api && uv run python ../web/components/flow/e2e/run_all.py
        [--only demo,accounts,join,add,run,settings] [--mode mock|real|both]
        [--out DIR] [--headed] [--unit]

It finds the scripts by file name next to this one: demo_path.py is area 'demo', each
sweep_<area>.py is area '<area>'. An area asked for with --only whose script is not there
(yet) is skipped with a note. Each script runs as its own process (sys.executable), on a
fresh pair of free ports, writing into <out>/<area>/. --mode goes to the sweep_* scripts
only; demo_path.py always runs against the offline API (and with --keep-going, so one
report covers every step). --unit first runs the web unit tests (node --test) in web/.

It writes <out>/summary.json:

    {"runs": [{"area", "exit", "seconds", "seams": <that run's seams.json counts, or null>}],
     "failedByOwner": {"pages": n, "ethan": n, "api": n}}

and prints one table. Exit 1 when any run exited non-zero, else 0. Without Playwright's
Chromium it prints the SKIP line and exits 0.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of web/

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402
from harness import API_DIR, OWNERS, SKIP_LINE, WEB_DIR, free_port, wait_port_free  # noqa: E402

DEMO_SCRIPT = "demo_path.py"
SWEEP_PREFIX = "sweep_"
KNOWN_AREAS: tuple[str, ...] = ("demo", "accounts", "join", "add", "run", "settings")
"""The areas this run knows about, in run order; any other sweep_<area>.py found runs after them."""

UNIT_AREA = "unit"
UNIT_COMMAND: tuple[str, ...] = (
    "node",
    "--test",
    "components/flow/__tests__/*.test.mjs",
    "components/agent/__tests__/*.test.mjs",
)
"""The web unit tests, run in web/ (node expands the globs itself)."""


# --------------------------------------------------------------------------- pure helpers


def script_area(name: str) -> str | None:
    """The area a script file name stands for: 'demo' for demo_path.py, '<area>' for sweep_<area>.py."""
    if name == DEMO_SCRIPT:
        return "demo"
    if name.startswith(SWEEP_PREFIX) and name.endswith(".py"):
        area = name[len(SWEEP_PREFIX) : -len(".py")]
        return area or None
    return None


def discover_scripts(directory: Path) -> list[tuple[str, Path]]:
    """(area, script) for demo_path.py and every sweep_*.py in `directory`.

    The known areas come first in KNOWN_AREAS order, then any other sweep by name. A
    script that is not there is simply not listed.
    """
    found: dict[str, Path] = {}
    for path in sorted(Path(directory).glob("*.py")):
        area = script_area(path.name)
        if area is not None and path.is_file():
            found[area] = path

    def order(area: str) -> tuple[int, str]:
        return (KNOWN_AREAS.index(area), "") if area in KNOWN_AREAS else (len(KNOWN_AREAS), area)

    return [(area, found[area]) for area in sorted(found, key=order)]


def select_scripts(
    found: list[tuple[str, Path]], only: Iterable[str] | None
) -> tuple[list[tuple[str, Path]], list[str]]:
    """The scripts to run (in `found` order) and the asked-for areas that have no script.

    `only` None means everything found. An asked-for area with no script is a note, not
    an error: some sweeps come from parallel work and may not be merged yet.
    """
    if only is None:
        return list(found), []
    wanted = [a for a in dict.fromkeys(only) if a]
    have = {area for area, _ in found}
    chosen = [(area, path) for area, path in found if area in wanted]
    missing = [a for a in wanted if a not in have]
    return chosen, missing


def build_command(
    area: str,
    script: Path,
    mode: str,
    out: Path,
    api_port: int,
    web_port: int,
    headed: bool,
) -> list[str]:
    """The command line for one script: its own --out <out>/<area> and ports.

    --mode goes to sweep scripts only; demo_path.py always runs real, with --keep-going.
    """
    cmd = [
        sys.executable,
        str(script),
        "--out",
        str(Path(out) / area),
        "--api-port",
        str(api_port),
        "--web-port",
        str(web_port),
    ]
    if area == "demo":
        cmd.append("--keep-going")
    else:
        cmd += ["--mode", mode]
    if headed:
        cmd.append("--headed")
    return cmd


def merge_counts(counts: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Add up seams.json "counts" blocks; a missing one (None, a run that wrote none) adds nothing.

    Returns {"total", "ok", "failed", "failedByOwner": {owner: n for every owner}}.
    """
    merged: dict[str, Any] = {"total": 0, "ok": 0, "failed": 0, "failedByOwner": dict.fromkeys(OWNERS, 0)}
    for c in counts:
        if not c:
            continue
        for key in ("total", "ok", "failed"):
            merged[key] += int(c.get(key, 0) or 0)
        for owner, n in (c.get("failedByOwner") or {}).items():
            merged["failedByOwner"][owner] = merged["failedByOwner"].get(owner, 0) + int(n or 0)
    return merged


def read_counts(out_dir: Path) -> dict[str, Any] | None:
    """The "counts" of <out_dir>/seams.json, or None when the run wrote none (a SKIP, a crash)."""
    try:
        data = json.loads((Path(out_dir) / "seams.json").read_text())
    except (OSError, ValueError):
        return None
    counts = data.get("counts") if isinstance(data, dict) else None
    return counts if isinstance(counts, dict) else None


def two_free_ports() -> tuple[int, int]:
    """Two distinct free ports: (api, web)."""
    api = free_port()
    web = free_port()
    while web == api:
        web = free_port()
    return api, web


def table(runs: list[dict[str, Any]], notes: list[str]) -> str:
    rows = [("area", "exit", "seconds", "checks", "pages", "api", "ethan", "out")]
    for r in runs:
        c = r.get("seams")
        by = (c or {}).get("failedByOwner") or {}
        rows.append(
            (
                r["area"],
                str(r["exit"]),
                f"{r['seconds']:.0f}",
                str(c["total"]) if c else "-",
                str(by.get("pages", 0)) if c else "-",
                str(by.get("api", 0)) if c else "-",
                str(by.get("ethan", 0)) if c else "-",
                r.get("out", ""),
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    lines = []
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(widths))) + "  " + row[-1])
        if row is rows[0]:
            lines.append("  ".join("-" * w for w in widths) + "  ---")
    lines += [f"note: {n}" for n in notes]
    return "\n".join(line.rstrip() for line in lines)


# --------------------------------------------------------------------------- running


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_all.py",
        description="Run the flow pages' demo path and sweeps one after another, then one summary.",
    )
    parser.add_argument(
        "--only",
        default=None,
        help=f"comma-separated areas to run (default: every script found; known: {','.join(KNOWN_AREAS)})",
    )
    parser.add_argument(
        "--mode", choices=harness.SWEEP_MODES, default="both", help="passed to the sweeps (default %(default)s)"
    )
    parser.add_argument("--out", type=Path, default=None, help="results root (default: a new temp dir)")
    parser.add_argument("--headed", action="store_true", help="show the browser windows")
    parser.add_argument("--unit", action="store_true", help="first run the web unit tests (node --test in web/)")
    return parser.parse_args(argv)


def run_one(area: str, cmd: list[str], cwd: Path, out: str) -> dict[str, Any]:
    print(f"\n######## {area}: {' '.join(cmd)}", flush=True)
    started = time.monotonic()
    try:
        code = subprocess.run(cmd, cwd=cwd, check=False).returncode  # noqa: S603 - fixed commands
    except OSError as exc:
        print(f"  could not start: {exc}", flush=True)
        code = 127
    return {"area": area, "exit": code, "seconds": round(time.monotonic() - started, 1), "out": out}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not harness.chromium_available():
        print(SKIP_LINE, flush=True)
        return 0

    only = [a.strip() for a in args.only.split(",")] if args.only else None
    chosen, missing = select_scripts(discover_scripts(HERE), only)
    notes = [f"no script for '{a}' here yet ({script_name(a)}): skipped" for a in missing]
    for n in notes:
        print(n, flush=True)

    out = harness.sweep_out_dir(args.out, prefix="stitch-run-all-")
    runs: list[dict[str, Any]] = []

    if args.unit:
        run = run_one(UNIT_AREA, list(UNIT_COMMAND), WEB_DIR, str(WEB_DIR))
        run["seams"] = None
        runs.append(run)

    for area, script in chosen:
        api_port, web_port = two_free_ports()
        cmd = build_command(area, script, args.mode, out, api_port, web_port, args.headed)
        run = run_one(area, cmd, API_DIR, str(out / area))
        run["seams"] = read_counts(out / area)
        runs.append(run)
        for port in (api_port, web_port):
            if not wait_port_free(port):
                notes.append(f"port {port} from '{area}' was still busy 30 s after it finished")

    merged = merge_counts([r["seams"] for r in runs])
    summary = {
        "runs": [{k: r[k] for k in ("area", "exit", "seconds", "seams")} for r in runs],
        "failedByOwner": merged["failedByOwner"],
    }
    path = out / "summary.json"
    path.write_text(json.dumps(summary, indent=2) + "\n")

    print("\n" + table(runs, notes))
    fails = merged["failedByOwner"]
    print(f"\nfailed by owner: {', '.join(f'{o} {fails.get(o, 0)}' for o in OWNERS)}")
    print(f"summary: {path}")
    return 1 if any(r["exit"] != 0 for r in runs) else 0


def script_name(area: str) -> str:
    return DEMO_SCRIPT if area == "demo" else f"{SWEEP_PREFIX}{area}.py"


if __name__ == "__main__":
    raise SystemExit(main())
