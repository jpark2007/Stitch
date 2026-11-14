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
    ("canes", 0.6),  # every form of a listed word counts (was 1.0 before the inflections fix)
    ("cane-ish", 0.6),
    ("canesugar", 1.0),  # still whole words only
    ("never slow", 0.75),  # negation is not handled (documented)
    ("tall and cheerful", 1.0),
])
def test_speed_scale(notes, expected):
    assert speed_scale(notes) == expected


def test_keyword_tables():
    hobbled = {"cane", "canes", "walker", "walkers", "limp", "limps", "limped", "limping",
               "shuffle", "shuffles", "shuffled", "shuffling"}
    slow = {"slowly", "slow", "slower", "slowest", "frail", "frailer", "elderly", "stiff", "stiffer", "stiffly",
            "carefully"}
    assert set(SLOW) == hobbled | slow
    assert {w for w, f in SLOW.items() if f == 0.6} == hobbled
    assert {w for w, f in SLOW.items() if f == 0.75} == slow
    assert FAST == dict.fromkeys(
        ("quickly", "quick", "quicker", "quickest", "fast", "faster", "fastest", "briskly", "brisk", "brisker",
         "hurry", "hurries", "hurried", "hurrying", "spry", "sprier", "spryer", "energetic"), 1.25)
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


# ------------------------------------------------------- bug hunt: odd phrasing in notes

@pytest.mark.parametrize(("notes", "expected"), [
    ("walks slower than everyone else", 0.75),
    ("the slowest walker in the family", 0.6),  # "walker" (0.6) beats "slowest" (0.75)
    ("limped ever since the war", 0.6),
    ("shuffled along in slippers", 0.6),
    ("has two canes, one for Sundays", 0.6),
    ("got stiffer every winter", 0.75),
    ("always in a hurry", 1.25),
    ("hurrying everywhere", 1.25),
    ("faster than any of the grandkids", 1.25),
    ("quicker on her feet than you'd think", 1.25),
    ("spryer than his brother", 1.25),
    ("faster than you'd think, for a man with a cane", 0.6),  # slow still wins
    ("Slower. Much slower.", 0.75),
])
def test_inflected_speed_words_count(notes, expected):
    """Bug: only the exact listed forms matched, so 'walks slower', 'limped' or 'faster than
    the grandkids' left the puppet at full speed (1.0)."""
    assert speed_scale(notes) == expected


@pytest.mark.parametrize("notes", ["slowcoach", "fastidious", "quickstep", "hurricane", "breakfast", "steadfast"])
def test_words_that_merely_contain_a_keyword_do_not_count(notes):
    assert speed_scale(notes) == 1.0


def test_speed_scale_reaches_the_walk():
    """A note's factor stretches the walk it times: 'limped' takes 1 / 0.6 as long."""
    from screenplay.motion import locomotion_keys

    _, plain, _ = locomotion_keys("gran", "average", 0.0, (0.0, 0.0, 0.5), (0.6, 0.0, 0.5), 1)
    _, slow, _ = locomotion_keys("gran", "average", 0.0, (0.0, 0.0, 0.5), (0.6, 0.0, 0.5), 1,
                                 speed_scale=speed_scale("limped"))
    assert slow == pytest.approx(plain / 0.6)


# ------------------------------------------------------------- a walk that climbs

def test_a_walk_that_climbs_takes_at_least_as_long_as_the_climb():
    """Bug: motion.locomotion_keys timed a walk by its ground distance alone (hypot(dx, dz)), so a
    walk with a small sideways step and a large climb (a hug that walks dad 0.02 across and 0.3 up
    the church steps) took 0.057 s, while the same climb straight up took 0.86 s. A walk is now
    timed by max(ground distance, |dy|)."""
    from screenplay.motion import SPEEDS, locomotion_keys

    _, straight_up, _ = locomotion_keys("dad", "average", 0.0, (0.0, 0.0, 0.5), (0.0, 0.3, 0.5), 1)
    _, almost_up, _ = locomotion_keys("dad", "average", 0.0, (0.0, 0.0, 0.5), (0.02, 0.3, 0.5), 1)
    assert straight_up == pytest.approx(0.3 / SPEEDS["walk"])
    assert almost_up >= straight_up
