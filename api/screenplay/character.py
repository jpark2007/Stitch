"""Character traits from the character sheet (ARCHITECTURE.md section 1 'Paper puppets', section 3 step 3).

Two things on a person's sheet change how their puppet acts:

- ``notes`` ("walks slowly with a cane") scale their walking and running speed. Build sets the
  base speed in motion.py; :func:`speed_scale` is the extra factor from the notes.
- ``item_prop_id`` names their signature item, a prop worn in the head socket
  (shared/CONTRACTS.md: sockets ``hand_l``, ``hand_r``, ``head``). The World puts it on the head
  when it is built, and :func:`worn_item_keys` gives the parent/position keys that attach it.

Pure and deterministic. This module imports no World at runtime, so state.py can import it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import schemas
from screenplay.body import _attach_keys
from screenplay.tracks import Key

if TYPE_CHECKING:
    from screenplay.state import World

__all__ = ["FAST", "SLOW", "WEARABLE_SIZES", "signature_item", "speed_scale", "worn_item_keys"]

_HOBBLED = 0.6  # a cane, a walker, a limp, a shuffle
_SLOW = 0.75
_QUICK = 1.25

SLOW: dict[str, float] = {
    **dict.fromkeys(("cane", "walker", "limp", "limps", "limping", "shuffle", "shuffles", "shuffling"), _HOBBLED),
    **dict.fromkeys(("slowly", "slow", "frail", "elderly", "stiff", "stiffly", "carefully"), _SLOW),
}
FAST: dict[str, float] = dict.fromkeys(
    ("quickly", "quick", "fast", "briskly", "brisk", "hurries", "hurried", "spry", "energetic"), _QUICK)

# Sizes a puppet can wear on its head; furniture, vehicles and buildings never auto-wear.
WEARABLE_SIZES = ("tiny", "hand", "carried")

_WORD = re.compile(r"[a-z]+")


def speed_scale(notes: str | None) -> float:
    """Walking/running speed factor from a person's notes.

    Matching is case-insensitive and on whole words only ("slowcoach" matches nothing). If any
    SLOW word appears, the result is the smallest SLOW factor among them, so "walks slowly with a
    cane" is 0.6; slow words win over fast ones. Otherwise any FAST word gives 1.25, and no match
    gives 1.0. Negation is not understood: "never slow" still reads as slow (0.75).
    """
    if not notes:
        return 1.0
    words = set(_WORD.findall(notes.lower()))
    slow = [SLOW[w] for w in words if w in SLOW]
    if slow:
        return min(slow)
    if any(w in FAST for w in words):
        return _QUICK
    return 1.0


def signature_item(person: schemas.Person) -> str | None:
    """The prop id of the person's signature item (worn in the head socket), or None."""
    return person.character.item_prop_id


def worn_item_keys(world: World, who: str, t: float) -> list[Key]:
    """The parent and position keys that put whatever ``who`` wears in the head socket there at ``t``.

    ``('prop:<item>', 'parent', t, 'puppet:<who>.head')`` then
    ``('prop:<item>', 'position', t, [0.0, 0.0, 0.0])``: the anchor sits on the head socket.
    These are exactly the attach keys verbs_objects.apply_put_on emits (via body.put_on_keys)
    when the hand carries the grip all the way to the head. When it cannot (a grip above the
    anchor), put_on_keys instead swaps the parent with the offset where the hand let go and adds
    a [0, 0, 0] position key PUT_ON_SETTLE_SECONDS later, ending in the same place.
    Returns [] when nothing is worn.
    """
    from screenplay.state import HEAD, StateError  # runtime import: state.py imports this module

    if who not in world.people:
        raise StateError("unknown_id", f"{who} is not in the cast")
    item = world.people[who].holding.get(HEAD)
    if item is None:
        return []
    return _attach_keys(item, t, f"puppet:{who}.{HEAD}", (0.0, 0.0, 0.0))
