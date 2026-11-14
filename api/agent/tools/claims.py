"""Process-wide id claims, so concurrent runs of one family never pick the same new id.

A generated set or prop takes a readable id made from its name ("grandmas_kitchen",
"trumpet_case_2"). Each run picks the id first and saves the record later: for a set the
save waits on the image model, which takes seconds. A check against the repo alone
therefore lets two runs of the same family pick the same free id in that window, and the
second save then lands on the first run's record. A per-run lock cannot prevent that,
because each run has its own.

``claim_id`` closes the gap. Under one module-level lock it walks the candidate ids and
takes the first one that no other run has claimed and that does not exist yet, and
records the claim before releasing the lock. A run that reaches the same name a moment
later sees the claim and moves on to the next candidate.

Claims are keyed by ``(family_id, kind, id)``, so another family, or another kind of
record (``"set"``, ``"prop"``), is never blocked by them. The owner is the run id.

How long a claim lasts: a claim only has to cover the gap between picking an id and
saving the record, because from then on the record exists in the repo and every caller's
``exists`` check skips the id anyway. So a tool calls ``release_claim`` right after the
save has returned. A claim that is never released (the save raised, the run died
before saving) simply stays for the life of the process; that only ever costs a suffix.
Releasing also keeps the registry from mattering beyond the one repo it guards: two
separate repos in one process (two test cases, a demo next to the API) that reuse a
family id do not push each other's ids to a suffix once their saves are done.

Single-process assumption: the registry lives in this process's memory, so it protects
runs that share one API process (runs.py drives every run of a process on threads in
that process). With several API workers or hosts, two processes could still claim the
same id; closing that would need an atomic insert in the repo (e.g. an insert that fails
on an existing ``_id``), which is out of scope here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

__all__ = ["claim_id", "owner_of", "release_claim", "reset_claims"]

_lock = threading.Lock()
_claims: dict[tuple[str, str, str], str] = {}


def claim_id(
    family_id: str,
    kind: str,
    owner: str,
    candidates: Iterable[str],
    exists: Callable[[str], bool],
) -> str:
    """Claim the first free candidate id for ``owner`` and return it.

    A candidate is free when no other owner has claimed it and ``exists(candidate)`` is
    False. A candidate this owner already claimed does not block it: claiming it again
    returns it as-is and records nothing new, so the call is idempotent for one owner
    (while the id does not exist yet). ``exists`` runs under the registry lock, so it
    should be a quick lookup that never calls back into this module.

    ``candidates`` may be infinite; it is consumed only up to the first free id. Raises
    ``ValueError`` when a finite sequence runs out without a free id.
    """
    with _lock:
        for candidate in candidates:
            holder = _claims.get((family_id, kind, candidate))
            if holder is not None and holder != owner:
                continue
            if exists(candidate):
                continue
            _claims[(family_id, kind, candidate)] = owner
            return candidate
    raise ValueError(f"no free {kind} id in family {family_id!r} among the candidates")


def release_claim(family_id: str, kind: str, owner: str, id: str) -> None:  # noqa: A002 - matches owner_of
    """Drop ``owner``'s claim on ``id`` once the record is saved (the repo guards it from then on).

    Does nothing when the id is unclaimed or claimed by another owner.
    """
    with _lock:
        key = (family_id, kind, id)
        if _claims.get(key) == owner:
            del _claims[key]


def owner_of(family_id: str, kind: str, id: str) -> str | None:  # noqa: A002 - the documented name
    """The owner holding the claim on ``id``, or None when nobody has claimed it."""
    with _lock:
        return _claims.get((family_id, kind, id))


def reset_claims() -> None:
    """Forget every claim. For tests only: a live process never needs it."""
    with _lock:
        _claims.clear()
