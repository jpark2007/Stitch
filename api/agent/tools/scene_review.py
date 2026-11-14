"""review_scene: a deterministic check of the storybook extras the compiler does not require.

DESIGN.md asks the agent for more than a playable scene: storybook pageText on every shot,
one click-to-explain text per cast member and prop, pull tabs that drive real prop parts or
states, and narration whose words are traceable to a telling. The compiler accepts a
screenplay without any of these, so `compile_screenplay` runs this review after a successful
compile and tells the director what is still missing.

Warnings are advice, not compile errors: they never fail a compile and never use a compile
round. Each warning is one line, `<where>: <problem>; <fix>`, e.g.::

    shot s1: no pageText; add one or two short storybook sentences (at most 30 words)

The checks never raise for a screenplay that validated as a `Screenplay`.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping

import schemas
from screenplay.schema import Interaction, NarrationLine, Screenplay

MAX_PAGE_TEXT_WORDS = 30

_SOURCE_FIX = 'set source to the telling sentence the words come from, like "telling:<authorId>:s2"'


def _num(x: float) -> str:
    return f"{x:g}"


def _pair(values: Iterable[float]) -> str:
    return "[" + ", ".join(_num(v) for v in values) + "]"


def _names(ids: Iterable[str]) -> str:
    return ", ".join(ids)


# ----------------------------------------------------------------------- page text


def _review_page_text(screenplay: Screenplay) -> list[str]:
    out = []
    for shot in screenplay.shots:
        text = (shot.page_text or "").strip()
        if not text:
            out.append(
                f"shot {shot.id}: no pageText; add one or two short storybook sentences "
                f"(at most {MAX_PAGE_TEXT_WORDS} words)"
            )
            continue
        words = len(text.split())
        if words > MAX_PAGE_TEXT_WORDS:
            out.append(f"shot {shot.id}: pageText has {words} words; cut it to at most {MAX_PAGE_TEXT_WORDS} words")
    return out


# --------------------------------------------------------------------- explanations


def _review_explanations(screenplay: Screenplay) -> list[str]:
    expected = [f"puppet:{pid}" for pid in screenplay.cast] + [f"prop:{pid}" for pid in screenplay.props]
    expected = list(dict.fromkeys(expected))
    counts = Counter(e.target for e in screenplay.explanations or [])
    out = []
    for target in expected:
        n = counts.get(target, 0)
        if n == 0:
            what = "who they are in this story" if target.startswith("puppet:") else "why it matters in this story"
            out.append(f"explanations: none for {target}; add one with a sentence or two about {what}")
        elif n > 1:
            out.append(f"explanations: {n} for {target}; keep exactly one")
    known = set(expected)
    for target in counts:  # insertion order = first appearance
        if target not in known:
            out.append(
                f'explanations: target "{target}" is not in the cast or props; '
                'point it at "puppet:<cast id>" or "prop:<prop id>", or remove it'
            )
    return out


# --------------------------------------------------------------------- interactions


def _review_prop_ref(
    where: str,
    kind: str,
    prop_id: str | None,
    screenplay: Screenplay,
) -> tuple[list[str], bool]:
    """Check drives.prop; returns (warnings, prop is usable for further checks)."""
    if not prop_id:
        return [f"{where}: {kind} has no prop; set drives.prop to one of the screenplay props"], False
    if prop_id not in screenplay.props:
        choices = _names(screenplay.props) if screenplay.props else "none yet"
        return [
            f'{where}: prop "{prop_id}" is not in the screenplay props; '
            f"add it to props or use one of them ({choices})"
        ], False
    return [], True


def _review_interaction(
    it: Interaction,
    screenplay: Screenplay,
    shot_ids: list[str],
    props: Mapping[str, schemas.Prop] | None,
) -> list[str]:
    where = f"interaction {it.id}"
    out = []
    if it.shot is not None and it.shot not in shot_ids:
        out.append(
            f'{where}: shot "{it.shot}" does not exist; use one of the shot ids ({_names(shot_ids)}) '
            "or leave shot out to show it on every shot"
        )
    if len(it.at) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in it.at):
        out.append(
            f"{where}: at {_pair(it.at)} is not on the page; use [u, v] with both between 0 and 1 "
            "(fractions from the top-left)"
        )

    d = it.drives
    if d.type == "scrub":
        if d.from_ is not None and d.to is not None and not d.from_ < d.to:
            out.append(
                f"{where}: scrub from {_num(d.from_)} is not before to {_num(d.to)}; "
                "make from smaller than to (timeline seconds)"
            )
    elif d.type == "prop_state":
        warnings, usable = _review_prop_ref(where, "prop_state", d.prop, screenplay)
        out += warnings
        if not d.states:
            out.append(f"{where}: prop_state has no states; list the states the tab steps through in drives.states")
        elif usable and props is not None and d.prop in props:
            prop = props[d.prop]
            have = list(prop.states)
            for state in d.states:
                if state not in prop.states:
                    fix = f"use one of its states ({_names(have)})" if have else "it has no states; drive a part or scrub instead"
                    out.append(f'{where}: prop "{d.prop}" has no state "{state}"; {fix}')
    elif d.type == "prop_part":
        warnings, usable = _review_prop_ref(where, "prop_part", d.prop, screenplay)
        out += warnings
        missing = [name for name, value in (("part", d.part), ("channel", d.channel)) if not value]
        if d.range is None:
            missing.append("range")
        if missing:
            out.append(f"{where}: prop_part has no {' or '.join(missing)}; set drives.{', drives.'.join(missing)}")
        if d.range is not None and (len(d.range) != 2 or not d.range[0] < d.range[1]):
            out.append(f"{where}: range {_pair(d.range)} is not [min, max] with min < max; give two numbers, smaller first")
        if usable and d.part and props is not None and d.prop in props:
            prop = props[d.prop]
            if d.part not in prop.parts:
                have = list(prop.parts)
                fix = f"use one of its parts ({_names(have)})" if have else "it has no moving parts; use prop_state or scrub instead"
                out.append(f'{where}: prop "{d.prop}" has no part "{d.part}"; {fix}')
    return out


def _review_interactions(screenplay: Screenplay, props: Mapping[str, schemas.Prop] | None) -> list[str]:
    interactions = screenplay.interactions or []
    shot_ids = [shot.id for shot in screenplay.shots]
    out = []
    for ident, n in Counter(it.id for it in interactions).items():
        if n > 1:
            out.append(f'interactions: id "{ident}" is used {n} times; give each interaction its own id')
    for it in interactions:
        out += _review_interaction(it, screenplay, shot_ids, props)

    if props is not None and not interactions:
        movable = [
            pid for pid in dict.fromkeys(screenplay.props) if pid in props and (props[pid].parts or props[pid].states)
        ]
        if movable:
            out.append(
                f"interactions: none, but {_names(movable)} can move or change; "
                "add a pull_tab that drives one of them (prop_part or prop_state)"
            )
    return out


# ------------------------------------------------------------------------ narration


def _review_lines(where: str, lines: list[NarrationLine]) -> list[str]:
    return [
        f"{where} {i}: no source; {_SOURCE_FIX}"
        for i, line in enumerate(lines, start=1)
        if not (line.source or "").strip()
    ]


def _review_narration(screenplay: Screenplay) -> list[str]:
    out = []
    for shot in screenplay.shots:
        out += _review_lines(f"shot {shot.id}, narration line", shot.narration)
    for author, shots in (screenplay.narrations or {}).items():
        for shot_id, lines in shots.items():
            out += _review_lines(f"narrations {author}, shot {shot_id}, line", lines)
    return out


# ---------------------------------------------------------------------------- entry


def review_scene(screenplay: Screenplay, *, props: Mapping[str, schemas.Prop] | None = None) -> list[str]:
    """Warnings about missing storybook extras, one line each: '<where>: <problem>; <fix>'.

    `props` (prop id -> saved Prop) enables the checks that need the rig: interaction states
    and parts must exist on the prop, and a prop with parts or states asks for a pull tab.
    """
    return (
        _review_page_text(screenplay)
        + _review_explanations(screenplay)
        + _review_interactions(screenplay, props)
        + _review_narration(screenplay)
    )
