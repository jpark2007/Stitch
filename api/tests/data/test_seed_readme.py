"""seed/README.md stays in step with the demo data it describes.

The README is the teammate-facing guide to the offline demo. These checks read the seed
data at test time (seed.demo: the moments, accounts and password, and the CLI's exit-code
constants; the screenplays' sets and props; the effects in the compiled timelines) and fail
when the README stops naming one of them, so adding a moment, set, prop, account, effect or
exit code without documenting it is caught. Offline and
fast: nothing here touches a database or the network.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from seed import demo

README_PATH = Path(demo.__file__).resolve().parent / "README.md"
MODULE_RE = re.compile(r"python -m (seed(?:\.[A-Za-z_]\w*)+)")


def _named(text: str, token: str) -> bool:
    """`token` appears in the README as inline code (`token`), the way ids are written."""
    return f"`{token}`" in text


def missing_memory_ids(text: str) -> list[str]:
    return [m for m in demo.DEMO_MEMORY_IDS if not _named(text, m)]


def missing_accounts(text: str) -> list[str]:
    return [a.email for a in demo.DEMO_ACCOUNTS if a.email not in text]


def used_set_ids(scenes: dict[str, demo.DemoScene]) -> list[str]:
    """Every set a seeded screenplay's shots use, in moment then shot order."""
    return list(dict.fromkeys(shot.set for scene in scenes.values() for shot in scene.screenplay.shots))


def missing_set_ids(text: str, scenes: dict[str, demo.DemoScene]) -> list[str]:
    return [s for s in used_set_ids(scenes) if not _named(text, s)]


def used_prop_ids(scenes: dict[str, demo.DemoScene]) -> list[str]:
    """Every prop a seeded memory uses (the memory's prop ids), in moment order."""
    return list(dict.fromkeys(pid for scene in scenes.values() for pid in scene.prop_ids))


def missing_prop_ids(text: str, scenes: dict[str, demo.DemoScene]) -> list[str]:
    return [p for p in used_prop_ids(scenes) if not _named(text, p)]


def exit_codes() -> dict[str, int]:
    """seed.demo's CLI exit-code constants (EXIT_OK, EXIT_BAD_DATA, ...)."""
    return {name: value for name, value in vars(demo).items() if name.startswith("EXIT_") and isinstance(value, int)}


def missing_exit_codes(text: str) -> list[str]:
    """Each constant must be on a README line (the exit-code table's row) that also gives its
    number, as a whole word, so `EXIT_NO_DATABASE` next to 3 passes and next to 13 does not."""
    missing = []
    for name, value in exit_codes().items():
        lines = [line for line in text.splitlines() if f"`{name}`" in line]
        if not any(re.search(rf"(?<![\w.]){value}(?![\w.])", line.replace(f"`{name}`", "")) for line in lines):
            missing.append(name)
    return missing


def compiled_effect_kinds(scenes: dict[str, demo.DemoScene]) -> list[str]:
    """Every effect kind in any compiled demo timeline's `effects`."""
    return sorted({e.kind for scene in scenes.values() for e in (scene.timeline.effects or [])})


def missing_effects(text: str, scenes: dict[str, demo.DemoScene]) -> list[str]:
    return [k for k in compiled_effect_kinds(scenes) if not _named(text, k)]


@pytest.fixture(scope="module")
def readme() -> str:
    return README_PATH.read_text()


@pytest.fixture(scope="module")
def scenes() -> dict[str, demo.DemoScene]:
    return demo.compile_demo_scenes()


def test_every_memory_id_is_in_the_readme(readme: str) -> None:
    assert demo.DEMO_MEMORY_IDS
    assert missing_memory_ids(readme) == []


def test_every_account_and_the_password_are_in_the_readme(readme: str) -> None:
    assert demo.DEMO_ACCOUNTS
    assert missing_accounts(readme) == []
    assert demo.DEMO_PASSWORD in readme


def test_every_set_a_screenplay_uses_is_in_the_readme(readme: str, scenes: dict[str, demo.DemoScene]) -> None:
    assert used_set_ids(scenes)
    assert missing_set_ids(readme, scenes) == []


def test_every_compiled_effect_is_in_the_readme(readme: str, scenes: dict[str, demo.DemoScene]) -> None:
    # The demo is meant to show effects: an empty list means the seed lost them.
    assert compiled_effect_kinds(scenes)
    assert missing_effects(readme, scenes) == []


def test_every_prop_a_seeded_memory_uses_is_in_the_readme(readme: str, scenes: dict[str, demo.DemoScene]) -> None:
    assert used_prop_ids(scenes)
    assert missing_prop_ids(readme, scenes) == []


def test_every_exit_code_is_in_the_readme_with_its_number(readme: str) -> None:
    codes = exit_codes()
    assert {"EXIT_OK", "EXIT_BAD_DATA", "EXIT_NO_DATABASE"} <= set(codes), codes
    assert missing_exit_codes(readme) == []


def test_the_scene_table_has_one_row_per_moment_in_year_order(readme: str) -> None:
    rows = [line for line in readme.splitlines() if line.startswith("| `")]
    first_cells = [row.split("|")[1].strip().strip("`") for row in rows]
    ids_in_table = [cell for cell in first_cells if cell in demo.DEMO_MEMORY_IDS]
    assert ids_in_table == list(demo.DEMO_MEMORY_IDS)
    for moment in demo.MOMENTS:
        row = next(r for r in rows if r.split("|")[1].strip() == f"`{moment.id}`")
        cells = [c.strip() for c in row.split("|")[1:-1]]
        assert cells[1] == str(moment.year), moment.id
        assert cells[2] == moment.title, moment.id


def test_the_offline_demo_and_smoke_commands_are_in_the_readme(readme: str) -> None:
    assert "python -m seed.serve" in readme
    assert "--memory-id" in readme
    # The quick start comes first.
    first_section = readme.split("\n## ", 2)[1]
    assert first_section.startswith("Quick start")
    assert "uv run python -m seed.serve" in first_section


def test_every_seed_module_the_readme_runs_is_importable(readme: str) -> None:
    modules = sorted(set(MODULE_RE.findall(readme)))
    assert "seed.serve" in modules
    unresolved = [m for m in modules if importlib.util.find_spec(m) is None]
    assert unresolved == []


def preflight_exit_codes() -> dict[str, int]:
    from seed import preflight

    return {name: value for name, value in vars(preflight).items() if name.startswith("EXIT_") and isinstance(value, int)}


def test_the_preflight_is_documented(readme: str) -> None:
    from db import client
    from seed import preflight

    section = readme.split("## Preflight a real MongoDB", 1)[1].split("\n## ", 1)[0]
    assert "uv run python -m seed.preflight" in section
    for flag in ("--json", "--timeout-ms", "--wait", "--skip-checkpointer"):
        assert flag in section, flag
    rows = ["connect", "server", "indexes", "round trip", "tree", "search", "checkpointer", "cleanup",
            *(f"vector index {n}" for n in client.VECTOR_INDEXES.values())]
    for row in rows:
        assert row in section, row
    for line in ("PREFLIGHT PASS", "PREFLIGHT PASS with N warnings", "PREFLIGHT FAIL"):
        assert line in section
    codes = preflight_exit_codes()
    assert codes == {"EXIT_OK": 0, "EXIT_FAILED": 1, "EXIT_NO_DATABASE": 3}
    for name, value in codes.items():
        row = next(line for line in section.splitlines() if f"`seed.preflight.{name}`" in line)
        assert row.startswith(f"| {value} |"), row
    assert preflight.UNSET_MESSAGE in section
    flat = " ".join(section.split())
    assert "IP access list" in flat and "at most 3 search indexes" in flat
    assert "[../db/REAL_MONGODB.md](../db/REAL_MONGODB.md)" in readme


def test_the_real_database_seed_is_one_command(readme: str) -> None:
    section = readme.split("## Seed a real MongoDB", 1)[1].split("\n## ", 1)[0]
    assert "uv run python -m seed.demo  " in section and "--reset" in section
    assert "<credentials redacted>" in section
    # The CLI reads .env itself: no command in the seed or run sections needs --env-file.
    run = readme.split("## Run the API on a real MongoDB", 1)[1].split("\n## ", 1)[0]
    for text in (section, run):
        commands = [line for line in text.splitlines() if line.startswith("uv run")]
        assert commands and not any("--env-file" in c for c in commands), commands
    not_verified = readme.split("## What is not verified", 1)[1]
    assert "preflight" in not_verified and "mongomock" in not_verified


def test_the_checks_fail_when_the_readme_drops_something(scenes: dict[str, demo.DemoScene]) -> None:
    """Each checker reports a removed id, set, prop, email, effect or exit code (guards against a
    vacuous pass)."""
    text = README_PATH.read_text()
    memory_id = demo.DEMO_MEMORY_IDS[-1]
    assert missing_memory_ids(text.replace(f"`{memory_id}`", "")) == [memory_id]
    set_id = used_set_ids(scenes)[-1]
    assert set_id in missing_set_ids(text.replace(f"`{set_id}`", ""), scenes)
    email = demo.DEMO_ACCOUNTS[-1].email
    assert missing_accounts(text.replace(email, "")) == [email]
    kind = compiled_effect_kinds(scenes)[0]
    assert kind in missing_effects(text.replace(f"`{kind}`", ""), scenes)
    prop_id = "folding_chair_sam"
    assert prop_id in used_prop_ids(scenes)
    assert missing_prop_ids(text.replace(f"`{prop_id}`", ""), scenes) == [prop_id]
    # An exit code documented with the wrong number, or not at all, is caught.
    row = next(line for line in text.splitlines() if "`EXIT_NO_DATABASE`" in line)
    assert missing_exit_codes(text.replace(row, row.replace("| 3 |", "| 13 |"))) == ["EXIT_NO_DATABASE"]
    assert missing_exit_codes(text.replace("`EXIT_BAD_DATA`", "")) == ["EXIT_BAD_DATA"]
