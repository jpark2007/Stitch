"""Timeline sanity checks: physical and contract invariants a compiled timeline must keep.

Test helper only (not collected by pytest: the file name does not start with ``test_``). Import
it as ``from timeline_sanity import sanity_problems``.

Everything here reads the timeline alone: no screenplay, no scene, no compiler module. The only
other input is the rig's pose table (shared/rig/poses.json), which is part of the contract the
stage plays. Interpolation follows shared/CONTRACTS.md: hold the first value before a track's
first key and the last after its last; numbers and lists are linear between keys; strings step.
Two keys at the same time are a step: the later one wins from that time on.

``sanity_problems(timeline)`` runs every function in ``CHECKS`` and returns all their problems,
each a string that starts with the check's name. A later wave adds checks (foot planting, props
across sets) by writing another ``check_<name>(timeline) -> list[str]`` and appending it to
``CHECKS``; keep each one small and timeline-only.

Checks:

- ``hands``: at every prop ``parent`` key time, and just after it, no socket (a puppet's hand,
  head or a prop's socket) parents two props, no prop parent track has two different values at
  one time, and no prop is its own ancestor.
- ``parent_position``: every key on a prop's ``parent`` track has a ``position`` key at the same
  time (CONTRACTS.md, "Prop position and parent").
- ``times``: every track key, effect ``t`` and ``t + dur``, caption ``t``, and narration line,
  word and caption time lies in ``[0, duration + 1e-6]``; track keys never go back in time;
  shots start at 0, are ordered and contiguous, ``start <= actionStart <= end``, and the last one
  ends at ``duration``.
- ``seated``: between two consecutive ``root.position`` keys at which both legs hold the sit pose
  (poses.json ``sit``, within 1e-3), no leg key takes a walk or run cycle frame value, and the
  root moves horizontally only when some prop on the ground (the vehicle) moves by the same
  ``(dx, dz)`` over the same interval, within 1e-3.
- ``finite``: no NaN or inf anywhere, ``root.facing`` is always 1 or -1, and every
  ``root.position`` and prop ``position`` value is three numbers.
"""

from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from screenplay.schema import Timeline

__all__ = [
    "CHECKS", "SEAT_TOL", "check_finite", "check_hands", "check_parent_position", "check_seated",
    "check_times", "sanity_problems", "value_at",
]

TIME_TOL = 1e-6
SEAT_TOL = 1e-3

_POSES = json.loads((Path(__file__).resolve().parents[3] / "shared" / "rig" / "poses.json").read_text())
LEG_BONES = ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower")
SIT: dict[str, float] = {b: float(_POSES["poses"]["sit"][b]) for b in LEG_BONES}
CYCLE_VALUES: dict[str, list[float]] = {
    # Walk and run frame values of each leg bone, except any that equal the sit value.
    b: sorted({float(f[b]) for c in _POSES["cycles"].values() for f in c["frames"] if b in f
               and abs(float(f[b]) - SIT[b]) > SEAT_TOL})
    for b in LEG_BONES
}

_SOCKET_PARENT = re.compile(r"^(puppet|prop):([^.]+)\.(.+)$")

Keys = list[tuple[float, Any]]
Check = Callable[[Timeline], list[str]]


# ----------------------------------------------------------------------------- helpers

def _tracks(tl: Timeline) -> dict[tuple[str, str], Keys]:
    return {(tr.target, tr.channel): [(float(k[0]), k[1]) for k in tr.keys] for tr in tl.tracks}


def _t(t: float) -> str:
    return f"t={round(t, 3) + 0.0:g}"


def value_at(keys: Keys, t: float, *, before: bool = False) -> Any:
    """A track's value at ``t`` (CONTRACTS.md interpolation). ``before=True`` gives the limit from
    the left, so a step key exactly at ``t`` does not count yet. None for an empty track."""
    if not keys:
        return None
    times = [k[0] for k in keys]
    i = bisect.bisect_left(times, t) if before else bisect.bisect_right(times, t)
    # keys[:i] are at or before t (strictly before, with before=True).
    if i == 0:
        return keys[0][1]
    t0, v0 = keys[i - 1]
    if i == len(keys) or isinstance(v0, str) or v0 is None:
        return v0
    t1, v1 = keys[i]
    if isinstance(v1, str) or v1 is None or t1 <= t0:
        return v0
    s = (t - t0) / (t1 - t0)
    if isinstance(v0, list):
        return [a + (b - a) * s for a, b in zip(v0, v1)]
    return v0 + (v1 - v0) * s


def _numbers(value: Any, path: str) -> Iterator[tuple[str, float]]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield path, float(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _numbers(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _numbers(v, f"{path}[{i}]")


# ------------------------------------------------------------------------------ checks

def check_hands(tl: Timeline) -> list[str]:
    """(a) No socket holds two props; no prop is in two places at once."""
    tracks = _tracks(tl)
    parents = {tgt[len("prop:"):]: keys for (tgt, ch), keys in tracks.items() if tgt.startswith("prop:") and ch == "parent"}
    out: list[str] = []
    for prop, keys in sorted(parents.items()):
        seen: dict[float, Any] = {}
        for t, v in keys:
            if t in seen and seen[t] != v:
                out.append(f"hands: {_t(t)}: prop:{prop} has two parents at once: {seen[t]!r} and {v!r}")
            seen[t] = v
    times = sorted({t for keys in parents.values() for t, _ in keys})
    samples: list[float] = []
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else t + 1.0
        samples += [t, (t + nxt) / 2]  # at the key, and just after it (before anything else changes)
    reported: set[tuple[str, tuple[str, ...]]] = set()
    for t in samples:
        now = {prop: value_at(keys, t) for prop, keys in parents.items()}
        by_socket: dict[str, list[str]] = defaultdict(list)
        for prop, parent in now.items():
            if isinstance(parent, str) and _SOCKET_PARENT.match(parent):
                by_socket[parent].append(prop)
        for socket, props in sorted(by_socket.items()):
            if len(props) > 1 and (socket, tuple(sorted(props))) not in reported:
                reported.add((socket, tuple(sorted(props))))
                kind = "hand" if socket.endswith((".hand_l", ".hand_r")) else "socket"
                out.append(f"hands: {_t(t)}: {kind} {socket} holds {len(props)} props: {', '.join(sorted(props))}")
        for prop in sorted(now):
            chain, cur = [prop], now[prop]
            while isinstance(cur, str) and cur.startswith("prop:"):
                base = cur[len("prop:"):].split(".", 1)[0]
                if base in chain:
                    out.append(f"hands: {_t(t)}: prop:{prop} is its own ancestor: {' -> '.join(chain + [base])}")
                    break
                chain.append(base)
                cur = now.get(base, "ground")
    return out


def check_parent_position(tl: Timeline) -> list[str]:
    """(b) Every parent key has a position key at the same time."""
    tracks = _tracks(tl)
    out: list[str] = []
    for (tgt, ch), keys in sorted(tracks.items()):
        if not tgt.startswith("prop:") or ch != "parent":
            continue
        pos_times = [t for t, _ in tracks.get((tgt, "position"), [])]
        for t, v in keys:
            i = bisect.bisect_left(pos_times, t - TIME_TOL)
            if not (i < len(pos_times) and abs(pos_times[i] - t) <= TIME_TOL):
                out.append(f"parent_position: {_t(t)}: {tgt} parent key {v!r} has no position key at the same time")
    return out


def check_times(tl: Timeline) -> list[str]:
    """(c) Every time lies within the timeline; tracks are sorted; shots tile [0, duration]."""
    out: list[str] = []
    hi = tl.duration + TIME_TOL

    def within(t: float | None, what: str) -> None:
        if t is not None and math.isfinite(t) and not (-TIME_TOL <= t <= hi):
            out.append(f"times: {what} at {_t(t)} is outside [0, {tl.duration:g}]")

    for tr in tl.tracks:
        times = [float(k[0]) for k in tr.keys]
        for t in times:
            within(t, f"{tr.target} {tr.channel} key")
        for a, b in zip(times, times[1:]):
            if b < a:
                out.append(f"times: {tr.target} {tr.channel}: key at {_t(b)} comes after one at {_t(a)}")
    for e in tl.effects:
        within(e.t, f"{e.kind} effect start")
        if e.dur is not None:
            within(e.t + e.dur, f"{e.kind} effect end")
    for c in tl.captions:
        within(c.t, f"caption {c.text!r}")
    for author, nt in sorted((tl.narrations or {}).items()):
        for c in nt.captions:
            within(c.t, f"{author}'s caption {c.text!r}")
        for line in nt.lines:
            within(line.t, f"{author}'s line {line.text!r}")
            for w in line.words or []:
                within(w.t, f"{author}'s word {w.word!r}")
    if not tl.shots:
        out.append("times: the timeline has no shots")
        return out
    if abs(tl.shots[0].start) > TIME_TOL:
        out.append(f"times: first shot {tl.shots[0].id} starts at {_t(tl.shots[0].start)}, not 0")
    for s in tl.shots:
        if not (s.start - TIME_TOL <= s.action_start <= s.end + TIME_TOL):
            out.append(f"times: shot {s.id}: start {s.start:g}, actionStart {s.action_start:g}, end {s.end:g} out of order")
    for a, b in zip(tl.shots, tl.shots[1:]):
        if abs(a.end - b.start) > TIME_TOL:
            out.append(f"times: shots not contiguous: {a.id} ends at {a.end:g}, {b.id} starts at {b.start:g}")
    if abs(tl.shots[-1].end - tl.duration) > TIME_TOL:
        out.append(f"times: last shot {tl.shots[-1].id} ends at {tl.shots[-1].end:g}, duration is {tl.duration:g}")
    return out


def _sitting(legs: dict[str, Keys], t: float, *, before: bool) -> bool:
    for b in LEG_BONES:
        v = value_at(legs.get(b, []), t, before=before)
        if abs((0.0 if v is None else float(v)) - SIT[b]) > SEAT_TOL:  # a bone with no track is at 0
            return False
    return True


def check_seated(tl: Timeline) -> list[str]:
    """(d) Nobody walks while seated."""
    tracks = _tracks(tl)
    ground_props: dict[str, tuple[Keys, Keys]] = {}
    for (tgt, ch), keys in tracks.items():
        if tgt.startswith("prop:") and ch == "position":
            ground_props[tgt] = (keys, tracks.get((tgt, "parent"), []))
    out: list[str] = []
    puppets = sorted({tgt for tgt, _ in tracks if tgt.startswith("puppet:")})
    for who in puppets:
        root = tracks.get((who, "root.position"), [])
        legs = {b: tracks.get((who, f"{b}.rot"), []) for b in LEG_BONES}
        for (t0, p0), (t1, p1) in zip(root, root[1:]):
            if t1 <= t0 or not (_sitting(legs, t0, before=False) and _sitting(legs, t1, before=True)):
                continue
            for b in LEG_BONES:
                for t, v in legs[b]:
                    if t0 < t < t1 and any(abs(float(v) - c) <= SEAT_TOL for c in CYCLE_VALUES[b]):
                        out.append(f"seated: {_t(t)}: {who} is seated but {b} takes walk/run cycle value {v:g}")
            dx, dz = p1[0] - p0[0], p1[2] - p0[2]
            if math.hypot(dx, dz) <= SEAT_TOL:
                continue
            carried = False
            for keys, parent in ground_props.values():
                if value_at(parent, t0) not in (None, "ground") or value_at(parent, t1, before=True) not in (None, "ground"):
                    continue
                q0, q1 = value_at(keys, t0), value_at(keys, t1, before=True)
                if q0 is not None and abs(q1[0] - q0[0] - dx) <= SEAT_TOL and abs(q1[2] - q0[2] - dz) <= SEAT_TOL:
                    carried = True
                    break
            if not carried:
                out.append(f"seated: {_t(t0)} to {_t(t1)}: {who} is seated but moves ({dx:+.3f}, {dz:+.3f}) "
                           f"and no prop moves with them")
    return out


def check_finite(tl: Timeline) -> list[str]:
    """(e) No NaN or inf; facing is 1 or -1; positions are 3-vectors."""
    out = [f"finite: {path} is {v}" for path, v in _numbers(tl.model_dump(), "timeline") if not math.isfinite(v)]
    for tr in tl.tracks:
        if tr.channel == "root.facing":
            for t, v in tr.keys:
                if isinstance(v, bool) or v not in (1, -1):
                    out.append(f"finite: {_t(t)}: {tr.target} root.facing is {v!r}, not 1 or -1")
        elif tr.channel == "root.position" or (tr.target.startswith("prop:") and tr.channel == "position"):
            for t, v in tr.keys:
                if not (isinstance(v, list) and len(v) == 3):
                    out.append(f"finite: {_t(t)}: {tr.target} {tr.channel} is {v!r}, not [x, y, z]")
    return out


CHECKS: list[Check] = [check_hands, check_parent_position, check_times, check_seated, check_finite]


def sanity_problems(timeline: Timeline) -> list[str]:
    """Every problem every check in CHECKS finds; empty when the timeline is sane."""
    return [p for check in CHECKS for p in check(timeline)]
