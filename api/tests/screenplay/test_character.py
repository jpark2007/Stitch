"""Character traits (api/screenplay/character.py): speed from notes, the signature item on the head."""

import pytest

import schemas
from screenplay import character
from screenplay.body import REACH_SECONDS
from screenplay.character import FAST, SLOW, WEARABLE_SIZES, signature_item, speed_scale, worn_item_keys
from screenplay.schema import Action, Shot
from screenplay.state import StateError, World
from screenplay.tracks import TrackSet
from screenplay.verbs_objects import apply_put_on


def person(pid: str, notes: str | None = None, item: str | None = None) -> schemas.Person:
    return schemas.Person(id=pid, family_id="f", name=pid.title(),
                          character={"color": "#123456", "notes": notes, "item_prop_id": item})


def prop(pid: str, size: str = "hand") -> schemas.Prop:
    return schemas.Prop(id=pid, name=pid, size=size, art={"body": None})


# ------------------------------------------------------------------ speed_scale

@pytest.mark.parametrize(("notes", "expected"), [
    (None, 1.0),
    ("", 1.0),
    ("walks slowly with a cane", 0.6),
    ("Slowly", 0.75),
    ("SLOWLY, but surely", 0.75),
    ("slowcoach", 1.0),
    ("walks briskly", 1.25),
    ("Spry and energetic", 1.25),
    ("hurries along, quick and fast", 1.25),
    ("frail but quick", 0.75),  # slow wins over fast
    ("brisk with a cane", 0.6),  # the smallest slow factor wins too
    ("limping, stiffly", 0.6),
    ("a cane-user", 0.6),  # punctuation splits words
    ("canes", 1.0),  # plurals are not in the lists; whole words only
    ("never slow", 0.75),  # negation is not handled (documented)
    ("tall and cheerful", 1.0),
])
def test_speed_scale(notes, expected):
    assert speed_scale(notes) == expected


def test_keyword_tables():
    assert set(SLOW) == {"cane", "walker", "limp", "limps", "limping", "shuffle", "shuffles", "shuffling",
                         "slowly", "slow", "frail", "elderly", "stiff", "stiffly", "carefully"}
    assert {w for w, f in SLOW.items() if f == 0.6} == {
        "cane", "walker", "limp", "limps", "limping", "shuffle", "shuffles", "shuffling"}
    assert {w for w, f in SLOW.items() if f == 0.75} == {
        "slowly", "slow", "frail", "elderly", "stiff", "stiffly", "carefully"}
    assert FAST == dict.fromkeys(
        ("quickly", "quick", "fast", "briskly", "brisk", "hurries", "hurried", "spry", "energetic"), 1.25)
    for word in (*SLOW, *FAST):  # every keyword matches on its own, in any case
        assert speed_scale(word) == speed_scale(word.upper()) == (SLOW.get(word) or FAST[word])
    assert WEARABLE_SIZES == ("tiny", "hand", "carried")


# --------------------------------------------------------------- signature_item

def test_signature_item():
    assert signature_item(person("dad", item="flat_cap")) == "flat_cap"
    assert signature_item(person("mom")) is None


# --------------------------------------------------------------- worn_item_keys

def test_worn_item_keys_empty_without_an_item():
    w = World([person("dad"), person("mom", item="anvil")], [prop("cap")])  # anvil is not passed in
    assert worn_item_keys(w, "dad", 0.0) == []
    assert worn_item_keys(w, "mom", 2.5) == []


def test_worn_item_keys_for_a_signature_item():
    w = World([person("dad", item="flat_cap")], [prop("flat_cap")])
    assert worn_item_keys(w, "dad", 1.5) == [
        ("prop:flat_cap", "parent", 1.5, "puppet:dad.head"),
        ("prop:flat_cap", "position", 1.5, [0.0, 0.0, 0.0]),
    ]


def test_worn_item_keys_unknown_person():
    w = World([person("dad")], [])
    with pytest.raises(StateError) as ei:
        worn_item_keys(w, "ghost", 0.0)
    assert (ei.value.code, ei.value.reason) == ("unknown_id", "ghost is not in the cast")


class RecordingTracks(TrackSet):
    """A TrackSet that also remembers every raw key it was given, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.raw: list = []

    def extend(self, keys) -> None:
        keys = list(keys)
        self.raw.extend(keys)
        super().extend(keys)


def test_worn_item_keys_match_put_on_exactly():
    """The keys that attach a hat put on with put_on are exactly worn_item_keys at that time."""
    w = World([person("dad")], [prop("cap")])
    w.begin_shot(Shot.model_validate({"id": "s1", "set": "room", "start": {"dad": {"at": "home", "holding": "cap"}}}),
                 {"home": (0.0, 0.0, 0.5)}, same_set_as_previous=False)
    tracks = RecordingTracks()
    t = 2.0
    w.now = t  # the handler contract: world.now is the action's shot time (action_start is 0)
    apply_put_on(w, tracks, Action.model_validate({"t": t, "do": "put_on", "who": "dad", "what": "cap"}), t, 0.0,
                 lambda name, _t: (0.0, 0.0, 0.5))
    put_on_head_keys = [k for k in tracks.raw if k[0] == "prop:cap"]
    t_attach = t + REACH_SECONDS
    assert put_on_head_keys == worn_item_keys(w, "dad", t_attach)
    assert [k[1] for k in put_on_head_keys] == ["parent", "position"]
    assert put_on_head_keys[0][3] == "puppet:dad.head"


def test_module_is_pure():
    """Same input, same output; nothing in the world changes."""
    w = World([person("dad", "walks slowly with a cane", "flat_cap")], [prop("flat_cap")])
    before = w.copy()
    assert worn_item_keys(w, "dad", 0.0) == worn_item_keys(w, "dad", 0.0)
    assert w == before
    assert character.speed_scale("walks slowly with a cane") == 0.6
