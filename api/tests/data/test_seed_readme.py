"""seed/README.md stays in step with the demo data it describes.

The README is the teammate-facing guide to the offline demo. These checks read the seed
data at test time (seed.demo: the moments, accounts and password; the screenplays' sets;
the effects in the compiled timelines) and fail when the README stops naming one of them,
so adding a moment, set, account or effect without documenting it is caught. Offline and
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


def test_the_checks_fail_when_the_readme_drops_something(scenes: dict[str, demo.DemoScene]) -> None:
    """Each checker reports a removed id, set, email or effect (guards against a vacuous pass)."""
    text = README_PATH.read_text()
    memory_id = demo.DEMO_MEMORY_IDS[-1]
    assert missing_memory_ids(text.replace(f"`{memory_id}`", "")) == [memory_id]
    set_id = used_set_ids(scenes)[-1]
    assert set_id in missing_set_ids(text.replace(f"`{set_id}`", ""), scenes)
    email = demo.DEMO_ACCOUNTS[-1].email
    assert missing_accounts(text.replace(email, "")) == [email]
    kind = compiled_effect_kinds(scenes)[0]
    assert kind in missing_effects(text.replace(f"`{kind}`", ""), scenes)
