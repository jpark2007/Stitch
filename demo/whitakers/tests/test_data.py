"""Loading a family data directory: what it becomes, and every problem it names."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from demo.whitakers import data as family_data
from demo.whitakers.data import WhitakerDataError, data_dir, load_family, media_id, reuse_screenplay
from demo.whitakers.tests.conftest import FID, FIXTURE_DIR, edit_json


@pytest.fixture(scope="module")
def loaded():
    return load_family(FIXTURE_DIR)


def errors_of(directory: Path) -> list[str]:
    with pytest.raises(WhitakerDataError) as caught:
        load_family(directory)
    return caught.value.errors


def test_the_data_dir_is_the_env_var_else_the_private_default(tmp_path):
    assert data_dir({family_data.DATA_DIR_ENV: str(tmp_path)}) == tmp_path.resolve()
    assert data_dir({}) == Path("~/stitch-private/demo-family/data").expanduser().resolve()


def test_the_fixture_loads_people_parents_first_with_accounts_linked(loaded):
    assert loaded.family_id == FID and loaded.name == "The Testers"
    order = [p.id for p in loaded.people]
    assert order.index("ada") < order.index("cleo") and order.index("ben") < order.index("cleo")
    by_id = {p.id: p for p in loaded.people}
    # Every account's person points at its user (Channels binds calls and texts by it).
    for account in loaded.accounts:
        assert by_id[account.person_id].user_id == account.user_id
    assert by_id["ada"].user_id is None
    assert by_id["ben"].spouse_ids == ["ada"] and by_id["ada"].spouse_ids == ["ben"]
    assert by_id["cleo"].parent_ids == ["ada", "ben"]
    assert by_id["cleo"].character.color == "#3d7ea6" and by_id["cleo"].character.item == "red scarf"
    # No color given: one is picked, different from everyone else's.
    assert by_id["ben"].character.color and by_id["ben"].character.color not in {
        p.character.color for p in loaded.people if p.id != "ben"
    }
    assert loaded.admin.person_id == "cleo"


def test_art_files_become_media_and_missing_art_is_the_placeholder(loaded):
    props = {p.id: p for p in loaded.props}
    books = props["tester_books"]
    assert books.family_id == FID
    assert books.art["body"] == f"media/{media_id(FID, 'art', 'tester_books.png')}"
    assert books.aspect > 0
    steps = next(s for s in loaded.sets if s.id == "tester_steps")
    assert steps.layers[0].art == f"media/{media_id(FID, 'art', 'tester_steps.layer0.png')}"
    assert steps.layers[1].art is None and steps.layers[2].art is None
    kinds = {m.media_id: m.kind for m in loaded.media}
    assert kinds[books.art["body"].removeprefix("media/")] == "cutout"


def test_moments_compile_oldest_first_with_tellings_in_order_and_photos(loaded):
    assert [m.id for m in loaded.moments] == ["tamales_1975", "books_1999"]
    books = loaded.moments[1]
    assert [t.author_id for t in books.tellings] == ["dev", "cleo"]
    assert books.tellings[1].photos == ("porch.png", "kitchen.png")
    assert books.timeline is not None and books.set_ids == ["tester_steps"]
    assert set(books.participant_ids) == {"dev", "cleo"}
    assert loaded.photo_ids["porch.png"] == media_id(FID, "photo", "porch.png")


def test_a_reused_demo_screenplay_is_recast_with_borrowed_sets_and_props(loaded):
    tamales = loaded.moments[0]
    assert tamales.reused == "demo_sunday_tamales"
    assert set(tamales.screenplay.cast) == {"cleo", "ada", "ben"}
    assert tamales.screenplay.memory_id == "tamales_1975"
    assert [t.author_id for t in tamales.tellings] == ["cleo"]
    assert tamales.tellings[0].photos == ("kitchen.png",)
    # The demo family's set and props, copied into this family.
    sets = {s.id: s for s in loaded.sets}
    assert "carmen_kitchen_1970" in sets and sets["carmen_kitchen_1970"].family_id == FID
    assert all(p.family_id == FID for p in loaded.props)


def test_reuse_names_the_cast_it_does_not_map():
    spec = family_data.ReuseSpec(screenplay="demo_sunday_tamales", cast={"mom": "cleo"})
    with pytest.raises(ValueError, match="grandma_carmen"):
        reuse_screenplay("x", spec)
    with pytest.raises(ValueError, match="not a demo screenplay"):
        reuse_screenplay("x", family_data.ReuseSpec(screenplay="nope", cast={}))


def test_the_summary_is_counts_only(loaded):
    line = family_data.summary(loaded)
    assert line == "4 people, 2 moments, 2 accounts, 2 photos, 2 art files"


def test_every_problem_is_named_at_once(data_copy):
    def family(value):
        value["people"][0]["parents"].append("nobody")
        value["people"].append({"id": "dad", "name": "Clash"})
        value["accounts"][1]["personId"] = "ghost"
        value["contacts"] = [{"personId": "dev", "imessage": "not-a-handle"}]

    edit_json(data_copy / "family.json", family)
    (data_copy / "photos" / "porch.png").unlink()
    errors = errors_of(data_copy)
    text = "\n".join(errors)
    assert "cleo's parent 'nobody' is not a person" in text
    assert "'dad' is a demo family (Maos) id" in text
    assert "personId 'ghost' is not a person" in text
    assert "imessage must be an E.164 phone number" in text
    assert "no photo photos/porch.png" in text


def test_a_screenplay_that_does_not_compile_names_the_moment_and_the_error(data_copy):
    path = data_copy / "moments" / "books_1999" / "screenplay.json"
    edit_json(path, lambda sp: sp["shots"][0]["start"]["cleo"].update(at="nowhere_mark"))
    [error] = errors_of(data_copy)
    assert error.startswith("moments/books_1999: the screenplay does not compile") and "nowhere_mark" in error


def test_tellings_must_match_the_narrators(data_copy):
    path = data_copy / "moments" / "books_1999" / "moment.json"
    edit_json(path, lambda m: m["tellings"].pop())
    [error] = errors_of(data_copy)
    assert "narrated by ['cleo', 'dev']" in error


def test_a_missing_named_art_file_is_a_warning_and_the_placeholder(data_copy):
    edit_json(data_copy / "props" / "props.json", lambda props: props[0]["art"].update(body="later.png"))
    loaded = load_family(data_copy)
    assert any("no art file art/later.png yet; placeholder art" in w for w in loaded.warnings)
    assert next(p for p in loaded.props if p.id == "tester_books").art["body"] is None


def test_art_dropped_in_later_is_picked_up_by_its_id(data_copy):
    shutil.copy(data_copy / "art" / "tester_books.png", data_copy / "art" / "tester_steps.layer2.png")
    loaded = load_family(data_copy)
    steps = next(s for s in loaded.sets if s.id == "tester_steps")
    assert steps.layers[2].art == f"media/{media_id(FID, 'art', 'tester_steps.layer2.png')}"


def test_unsupported_photo_types_are_refused(data_copy):
    (data_copy / "photos" / "extra.heic").write_bytes(b"x")
    path = data_copy / "moments" / "books_1999" / "moment.json"
    edit_json(path, lambda m: m["tellings"][0].update(photos=["extra.heic"]))
    assert any("convert HEIC to JPEG first" in e for e in errors_of(data_copy))


def test_a_missing_directory_is_one_error(tmp_path):
    assert errors_of(tmp_path / "missing") == [
        "the data directory does not exist (set STITCH_WHITAKERS_DIR or --data-dir)"
    ]


def test_contacts_are_imessage_handles(data_copy):
    edit_json(data_copy / "family.json", lambda f: f.update(contacts=[
        {"personId": "dev", "imessage": "Dev@Testers.Example"},
        {"personId": "cleo", "imessage": "+15555550123", "cadenceDays": 3},
    ]))
    loaded = load_family(data_copy)
    assert [(c.person_id, c.handle, c.cadence_days) for c in loaded.contacts] == [
        ("dev", "dev@testers.example", 7), ("cleo", "+15555550123", 3),
    ]
