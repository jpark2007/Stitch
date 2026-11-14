"""Set and prop ids never collide between concurrent runs of one family (agent/tools/claims.py).

The bug: each run reserved a set id under its own lock and saved the set only after the
image model returned, so a second run of the same family that reserved meanwhile got the
same id and its save merged into the first run's set. Props had the same gap between
picking an id and saving. These tests hold both runs inside the art call at once with a
barrier, which is exactly that window.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

import schemas
from agent.repo import InMemoryRepo
from agent.run_info import RunInfo
from agent.tools.art import ArtService, Cutout
from agent.tools.claims import claim_id, owner_of, release_claim, reset_claims
from agent.tools.props import make_prop_tools
from agent.tools.sets import make_set_tools
from schemas import SetDef, SetLayer

FAM = "fam"
OTHER = "fam_other"
KITCHEN = "Grandma's kitchen"
KITCHEN_ID = "grandmas_kitchen"
WAIT = 5.0
"""Seconds a barrier waits before failing the test instead of hanging it."""


@pytest.fixture(autouse=True)
def _fresh_claims() -> Any:
    reset_claims()
    yield
    reset_claims()


def make_run(run_id: str, family_id: str = FAM) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        family_id=family_id,
        user_id=f"user_{run_id}",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="Sunday lunch in Grandma's kitchen.", year=1988),
    )


def tools(factory: Callable[..., list[Any]], repo: InMemoryRepo, run: RunInfo, art: ArtService) -> dict[str, Any]:
    return {t.name: t for t in factory(repo, run, art)}


class BarrierArt(ArtService):
    """Placeholder art that first waits until every party is inside the art call.

    That is the window a real image model leaves open between picking an id and saving.
    """

    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__(None, None)
        self.barrier = barrier

    def make_layer(self, prompt: str, *, layer: Any, reference_images: Sequence[bytes] = (),
                   size: tuple[int, int] = (2048, 1024), run_id: str | None = None) -> Cutout | None:
        self.barrier.wait(timeout=WAIT)
        return None

    def make_cutout(self, prompt: str, *, reference_images: Sequence[bytes] = (),
                    size: tuple[int, int] = (1024, 1024), run_id: str | None = None) -> Cutout | None:
        self.barrier.wait(timeout=WAIT)
        return None


def run_together(*calls: Callable[[], str]) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        outs = [f.result(timeout=2 * WAIT) for f in [pool.submit(c) for c in calls]]
    for out in outs:
        assert not out.startswith("error"), out
    return [json.loads(out) for out in outs]


# ------------------------------------------------------------------------ claim_id


def test_claim_id_is_idempotent_for_the_same_owner() -> None:
    never = lambda _c: False  # noqa: E731
    assert claim_id(FAM, "set", "ra", ["a", "a_2"], never) == "a"
    assert claim_id(FAM, "set", "ra", ["a", "a_2"], never) == "a"
    assert owner_of(FAM, "set", "a") == "ra"
    assert owner_of(FAM, "set", "a_2") is None


def test_claim_id_skips_an_id_claimed_by_another_owner() -> None:
    never = lambda _c: False  # noqa: E731
    assert claim_id(FAM, "set", "ra", ["a", "a_2"], never) == "a"
    assert claim_id(FAM, "set", "rb", ["a", "a_2", "a_3"], never) == "a_2"
    assert claim_id(FAM, "set", "rc", ["a", "a_2", "a_3"], never) == "a_3"
    assert [owner_of(FAM, "set", i) for i in ("a", "a_2", "a_3")] == ["ra", "rb", "rc"]


def test_claim_id_skips_ids_that_exist() -> None:
    taken = {"a", "a_2"}
    assert claim_id(FAM, "prop", "ra", ["a", "a_2", "a_3"], taken.__contains__) == "a_3"
    assert owner_of(FAM, "prop", "a") is None
    # An id this owner claimed but that now exists is skipped too: a second prop of the
    # same name in one run gets a new id instead of overwriting the first.
    assert claim_id(FAM, "prop", "ra", ["b"], lambda _c: False) == "b"
    assert claim_id(FAM, "prop", "ra", ["b", "b_2"], {"b"}.__contains__) == "b_2"


def test_claims_are_scoped_by_family_and_kind() -> None:
    never = lambda _c: False  # noqa: E731
    assert claim_id(FAM, "set", "ra", ["a"], never) == "a"
    assert claim_id(OTHER, "set", "rb", ["a"], never) == "a"
    assert claim_id(FAM, "prop", "rb", ["a"], never) == "a"


def test_claim_id_consumes_only_what_it_needs_and_fails_when_a_finite_list_runs_out() -> None:
    def endless() -> Any:
        n = 0
        while True:
            n += 1
            yield f"x_{n}"

    assert claim_id(FAM, "set", "ra", endless(), lambda c: c in {"x_1", "x_2"}) == "x_3"
    with pytest.raises(ValueError):
        claim_id(FAM, "set", "rb", ["x_3"], lambda _c: False)


def test_release_claim_drops_only_the_owners_claim() -> None:
    never = lambda _c: False  # noqa: E731
    claim_id(FAM, "set", "ra", ["a"], never)
    release_claim(FAM, "set", "rb", "a")
    assert owner_of(FAM, "set", "a") == "ra"
    release_claim(FAM, "set", "ra", "a")
    assert owner_of(FAM, "set", "a") is None
    release_claim(FAM, "set", "ra", "a")  # releasing twice is harmless
    assert claim_id(FAM, "set", "rb", ["a"], never) == "a"


def test_concurrent_claims_never_hand_out_one_id_twice() -> None:
    barrier = threading.Barrier(8)

    def claim(owner: str) -> str:
        barrier.wait(timeout=WAIT)
        return claim_id(FAM, "set", owner, (f"a_{n}" for n in range(1, 100)), lambda _c: False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(claim, [f"r{i}" for i in range(8)]))
    assert len(set(got)) == 8


# ----------------------------------------------------------------- sets: the bug


def test_two_runs_painting_the_same_place_at_once_get_two_sets() -> None:
    """The bug report: both runs reserved 'grandmas_kitchen' while the other was still in
    the image model, and the second save merged into the first run's set."""
    repo = InMemoryRepo()
    art = BarrierArt(threading.Barrier(2))
    ra = tools(make_set_tools, repo, make_run("ra"), art)
    rb = tools(make_set_tools, repo, make_run("rb"), art)

    out_a, out_b = run_together(
        lambda: ra["generate_set_layer"].invoke({
            "place": KITCHEN, "layer": "back", "description": "yellow tiles and a lace curtain",
            "marks": {"stove": [0.3, 0.0]},
        }),
        lambda: rb["generate_set_layer"].invoke({
            "place": KITCHEN, "layer": "back", "description": "green cupboards and a radio",
            "marks": {"table": [0.7, 0.0]},
        }),
    )

    assert out_a["setId"] != out_b["setId"]
    assert {out_a["setId"], out_b["setId"]} == {KITCHEN_ID, f"{KITCHEN_ID}_2"}
    assert {s.id for s in repo.list_sets(FAM)} == {out_a["setId"], out_b["setId"]}

    set_a = repo.get_set(FAM, out_a["setId"])
    set_b = repo.get_set(FAM, out_b["setId"])
    assert set_a.description == "yellow tiles and a lace curtain"
    assert set_a.marks == {"stove": [0.3, 0.0]}
    assert set_b.description == "green cupboards and a radio"
    assert set_b.marks == {"table": [0.7, 0.0]}
    for made in (set_a, set_b):
        assert [la.depth for la in made.layers] == [0]
        assert made.name == KITCHEN
    # The claims are dropped once the sets are saved; the repo guards the ids from there.
    assert owner_of(FAM, "set", KITCHEN_ID) is None
    assert owner_of(FAM, "set", f"{KITCHEN_ID}_2") is None


def test_each_run_keeps_building_its_own_set_after_the_race() -> None:
    repo = InMemoryRepo()
    art = BarrierArt(threading.Barrier(2))
    ra = tools(make_set_tools, repo, make_run("ra"), art)
    rb = tools(make_set_tools, repo, make_run("rb"), art)
    out_a, out_b = run_together(
        lambda: ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "a back"}),
        lambda: rb["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "b back"}),
    )
    # Both runs paint their middle layers at once too; each lands on its own set.
    mid_a, mid_b = run_together(
        lambda: ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": "middle", "description": "a middle"}),
        lambda: rb["generate_set_layer"].invoke({"place": KITCHEN, "layer": "middle", "description": "b middle"}),
    )
    assert (mid_a["setId"], mid_b["setId"]) == (out_a["setId"], out_b["setId"])
    assert repo.get_set(FAM, out_a["setId"]).description == "a back; a middle"
    assert repo.get_set(FAM, out_b["setId"]).description == "b back; b middle"
    assert len(repo.list_sets(FAM)) == 2


def test_a_place_claimed_by_another_run_that_has_not_saved_yet_is_skipped() -> None:
    repo = InMemoryRepo()
    claim_id(FAM, "set", "rb", [KITCHEN_ID], lambda _c: False)  # rb is still in the image model
    ra = tools(make_set_tools, repo, make_run("ra"), ArtService(None, None))
    out = json.loads(ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "x"}))
    assert out["setId"] == f"{KITCHEN_ID}_2"
    assert repo.get_set(FAM, KITCHEN_ID) is None


# --------------------------------------------------------- sets: behaviour kept


def test_one_run_painting_three_layers_of_a_place_makes_one_set() -> None:
    repo = InMemoryRepo()
    ra = tools(make_set_tools, repo, make_run("ra"), ArtService(None, None))
    outs = [
        json.loads(ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": layer, "description": layer}))
        for layer in ("back", "middle", "front")
    ]
    assert {o["setId"] for o in outs} == {KITCHEN_ID}
    assert [s.id for s in repo.list_sets(FAM)] == [KITCHEN_ID]
    assert [la.depth for la in repo.get_set(FAM, KITCHEN_ID).layers] == [0, 1, 2]


def test_two_families_painting_the_same_place_at_once_both_get_the_bare_slug() -> None:
    repo = InMemoryRepo()
    art = BarrierArt(threading.Barrier(2))
    ra = tools(make_set_tools, repo, make_run("ra", FAM), art)
    rb = tools(make_set_tools, repo, make_run("rb", OTHER), art)
    out_a, out_b = run_together(
        lambda: ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "ours"}),
        lambda: rb["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "theirs"}),
    )
    assert out_a["setId"] == out_b["setId"] == KITCHEN_ID
    assert repo.get_set(FAM, KITCHEN_ID).description == "ours"
    assert repo.get_set(OTHER, KITCHEN_ID).description == "theirs"


def test_a_set_saved_by_another_run_before_reservation_gives_a_suffixed_id() -> None:
    repo = InMemoryRepo()
    rb = tools(make_set_tools, repo, make_run("rb"), ArtService(None, None))
    rb["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "rb's kitchen"})
    before = repo.get_set(FAM, KITCHEN_ID)

    ra = tools(make_set_tools, repo, make_run("ra"), ArtService(None, None))
    out = json.loads(ra["generate_set_layer"].invoke(
        {"place": KITCHEN, "layer": "back", "description": "ra's kitchen", "marks": {"sink": [0.5, 0.0]}}))
    assert out["setId"] == f"{KITCHEN_ID}_2"
    assert repo.get_set(FAM, KITCHEN_ID) == before
    assert repo.get_set(FAM, f"{KITCHEN_ID}_2").marks == {"sink": [0.5, 0.0]}


# ------------------------------------------------- sets: never merge into a stranger


class IntruderArt(ArtService):
    """While the run draws its layer, someone outside the claim registry saves a set at the
    id the run reserved (as another API process could)."""

    def __init__(self, repo: InMemoryRepo) -> None:
        super().__init__(None, None)
        self.repo = repo
        self.intruded = False

    def make_layer(self, prompt: str, *, layer: Any, reference_images: Sequence[bytes] = (),
                   size: tuple[int, int] = (2048, 1024), run_id: str | None = None) -> Cutout | None:
        if not self.intruded:
            self.intruded = True
            self.repo.save_set(FAM, SetDef(
                id=KITCHEN_ID, family_id=FAM, name="Someone else's kitchen", description="not ours",
                layers=[SetLayer(art=None, depth=0, height=1.0, color="#123456")],
                marks={"door": [0.1, 0.0]},
            ))
        return None


def test_a_set_that_appears_at_the_reserved_id_is_never_merged_into(caplog: pytest.LogCaptureFixture) -> None:
    repo = InMemoryRepo()
    art = IntruderArt(repo)
    ra = tools(make_set_tools, repo, make_run("ra"), art)

    with caplog.at_level(logging.WARNING, logger="agent.tools.sets"):
        out = json.loads(ra["generate_set_layer"].invoke(
            {"place": KITCHEN, "layer": "middle", "description": "our counter", "marks": {"sink": [0.5, 0.0]}}))
    intruder = repo.get_set(FAM, KITCHEN_ID)

    assert out["setId"] == f"{KITCHEN_ID}_2", "the result reports the id actually used"
    assert any("saving under a new id" in r.getMessage() for r in caplog.records)
    assert intruder.description == "not ours"
    assert [la.depth for la in intruder.layers] == [0]
    assert intruder.marks == {"door": [0.1, 0.0]}
    ours = repo.get_set(FAM, f"{KITCHEN_ID}_2")
    assert ours.description == "our counter" and ours.marks == {"sink": [0.5, 0.0]}
    assert [la.depth for la in ours.layers] == [1]

    # Later calls for the place in this run go to the moved set.
    out2 = json.loads(ra["generate_set_layer"].invoke({"place": KITCHEN, "layer": "back", "description": "our wall"}))
    assert out2["setId"] == f"{KITCHEN_ID}_2"
    assert [la.depth for la in repo.get_set(FAM, f"{KITCHEN_ID}_2").layers] == [0, 1]
    assert repo.get_set(FAM, KITCHEN_ID) == intruder


# ----------------------------------------------------------------------- props


class SlowSaveRepo(InMemoryRepo):
    """InMemoryRepo whose prop save takes a moment, like a database write, so a run that
    checks for the id in the meantime still sees it free."""

    def save_prop(self, family_id: str, prop: schemas.Prop) -> schemas.Prop:
        time.sleep(0.2)
        return super().save_prop(family_id, prop)


def test_two_runs_generating_the_same_prop_at_once_get_two_props() -> None:
    repo = SlowSaveRepo()
    art = BarrierArt(threading.Barrier(2))
    ra = tools(make_prop_tools, repo, make_run("ra"), art)
    rb = tools(make_prop_tools, repo, make_run("rb"), art)

    out_a, out_b = run_together(
        lambda: ra["generate_prop"].invoke(
            {"name": "trumpet case", "description": "black leather, brass clasps", "size": "carried"}),
        lambda: rb["generate_prop"].invoke(
            {"name": "trumpet case", "description": "brown cardboard, a torn handle", "size": "carried"}),
    )

    assert out_a["id"] != out_b["id"]
    assert {out_a["id"], out_b["id"]} == {"trumpet_case", "trumpet_case_2"}
    prop_a = repo.get_prop(FAM, out_a["id"])
    prop_b = repo.get_prop(FAM, out_b["id"])
    assert prop_a.description == "black leather, brass clasps"
    assert prop_b.description == "brown cardboard, a torn handle"
    assert {p.id for p in repo.list_props(FAM) if p.family_id == FAM} == {out_a["id"], out_b["id"]}
    assert owner_of(FAM, "prop", "trumpet_case") is None
    assert owner_of(FAM, "prop", "trumpet_case_2") is None


def test_a_prop_id_claimed_by_another_run_is_skipped_and_one_run_still_suffixes_its_own() -> None:
    repo = InMemoryRepo()
    claim_id(FAM, "prop", "rb", ["trumpet_case"], lambda _c: False)
    ra = tools(make_prop_tools, repo, make_run("ra"), ArtService(None, None))
    args = {"name": "trumpet case", "description": "a case", "size": "carried"}
    first = json.loads(ra["generate_prop"].invoke(args))
    second = json.loads(ra["generate_prop"].invoke(args))
    assert (first["id"], second["id"]) == ("trumpet_case_2", "trumpet_case_3")
    assert repo.get_prop(FAM, "trumpet_case") is None


def test_another_family_can_use_the_same_prop_id_at_the_same_time() -> None:
    repo = InMemoryRepo()
    art = BarrierArt(threading.Barrier(2))
    ra = tools(make_prop_tools, repo, make_run("ra", FAM), art)
    rb = tools(make_prop_tools, repo, make_run("rb", OTHER), art)
    args = {"name": "trumpet case", "description": "a case", "size": "carried"}
    out_a, out_b = run_together(lambda: ra["generate_prop"].invoke(args), lambda: rb["generate_prop"].invoke(args))
    assert out_a["id"] == out_b["id"] == "trumpet_case"
