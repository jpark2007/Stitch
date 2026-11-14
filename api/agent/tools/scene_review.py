"""Scene review: deterministic checks of what a compile accepts but the storybook still needs.

`review_scene(screenplay)` checks the storybook extras the compiler does not require.
DESIGN.md asks the agent for more than a playable scene: storybook pageText on every shot,
one click-to-explain text per cast member and prop, pull tabs that drive real prop parts or
states, and narration whose words are traceable to a telling.

`review_timeline(screenplay, timeline)` checks the compiled timeline, i.e. what the stage will
actually play: pull-tab scrub ranges that are empty or outside [0, duration], explanations
whose puppet or prop has no track, pageText that is never on the page while its shot plays,
and narration cues that land outside the timeline.

`compile_screenplay` runs both after a successful compile and tells the director what is
still missing. Warnings are advice, not compile errors: they never fail a compile and never
use a compile round. Each warning is one line, `<where>: <problem>; <fix>`, e.g.::

    shot s1: no pageText; add one or two short storybook sentences (at most 30 words)

The checks never raise for a screenplay and timeline that validated as their models.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping

import schemas
from screenplay.schema import Interaction, NarrationLine, Screenplay, Timeline, TimelineShot

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
                "make from smaller than to (seconds from the shot start)"
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


# ============================================================ the compiled timeline
#
# review_timeline looks at what the stage will actually play. It reads timeline time (seconds
# from the start of the timeline, shared/CONTRACTS.md "Timeline") but phrases every fix in
# screenplay terms, because the director edits the screenplay, not the timeline.

_EPS = 1e-3  # the compilers round times to 4 decimals; anything closer than this is "on the edge"


def _outside(t: float, duration: float) -> bool:
    return not (math.isfinite(t) and -_EPS <= t <= duration + _EPS)


def _shot_length(ts: TimelineShot) -> float:
    """How long a shot runs in shot time (screenplay seconds from its t = 0)."""
    return max(ts.end - ts.action_start, 0.0)


# ------------------------------------------------------------------- interactions


def _review_timeline_interaction(
    it: Interaction,
    timeline: Timeline,
    shots: dict[str, TimelineShot],
    targets: set[str],
) -> list[str]:
    d = it.drives
    if d.type != "scrub":
        return []
    where = f"interaction {it.id}"
    out = []
    ends = [(name, v) for name, v in (("from", d.from_), ("to", d.to)) if v is not None]
    bad = [(name, v) for name, v in ends if _outside(v, timeline.duration)]
    if bad:
        said = " and ".join(f"{name} {_num(v)}s" for name, v in bad)
        verb = "are" if len(bad) > 1 else "is"
        ts = shots.get(it.shot) if it.shot is not None else None
        if ts is not None:
            fix = f"keep from and to between 0 and {_num(round(_shot_length(ts), 2))} (seconds from the start of shot {ts.id})"
        else:
            fix = "keep from and to inside the shot the tab belongs to (seconds from that shot's start)"
        out.append(f"{where}: scrub {said} {verb} outside the timeline (0 to {_num(timeline.duration)}s); {fix}")
    if d.from_ is not None and d.to is not None and not d.to - d.from_ > _EPS:
        out.append(
            f"{where}: scrub from {_num(d.from_)}s to {_num(d.to)}s covers no time in the timeline; "
            "make from smaller than to so pulling the tab plays something"
        )
    for target in dict.fromkeys(d.targets or []):
        if target not in targets:
            out.append(
                f'{where}: scrub target "{target}" has no track in the timeline, so pulling the tab moves nothing; '
                'target a "puppet:<cast id>" or "prop:<prop id>" that is on stage, or leave targets out'
            )
    return out


def _review_timeline_interactions(timeline: Timeline, shots: dict[str, TimelineShot], targets: set[str]) -> list[str]:
    out = []
    for it in timeline.interactions or []:
        out += _review_timeline_interaction(it, timeline, shots, targets)
    return out


# ------------------------------------------------------------------- explanations


def _review_timeline_explanations(screenplay: Screenplay, timeline: Timeline, targets: set[str]) -> list[str]:
    explanations = timeline.explanations if timeline.explanations is not None else screenplay.explanations
    out = []
    for target in dict.fromkeys(e.target for e in explanations or []):
        kind, _, ident = target.partition(":")
        if kind not in ("puppet", "prop") or not ident or target in targets:
            continue  # a malformed target is review_scene's warning
        if kind == "puppet":
            fix = f"bring {ident} on stage in a shot (start or enter), or remove the explanation"
        else:
            fix = f"put {ident} on stage (propStart, or someone holding it), or remove the explanation"
        out.append(
            f'explanations: target "{target}" has no track in the timeline, so there is nothing to click; {fix}'
        )
    return out


# ---------------------------------------------------------------------- page text


def _page_text_window(index: int, timeline: Timeline) -> tuple[float, float, str]:
    """When timeline shot `index` holds the page: its [start, end], cut to [0, duration] and to
    the moment the next shot takes the page. Returns (begin, end, what limited it)."""
    ts = timeline.shots[index]
    begin, end = max(ts.start, 0.0), min(ts.end, timeline.duration)
    why = f"the shot runs {_num(ts.start)}s to {_num(ts.end)}s, the timeline 0 to {_num(timeline.duration)}s"
    if index + 1 < len(timeline.shots):
        nxt = timeline.shots[index + 1]
        if nxt.start < end:
            end = nxt.start
            why += f", and shot {nxt.id} takes the page at {_num(nxt.start)}s"
    return begin, end, why


def _review_timeline_page_text(screenplay: Screenplay, timeline: Timeline) -> list[str]:
    index = {ts.id: i for i, ts in reversed(list(enumerate(timeline.shots)))}  # first occurrence wins
    out = []
    for shot in screenplay.shots:
        text = (shot.page_text or "").strip()
        if not text:
            continue  # review_scene asks for it
        where = f"shot {shot.id}"
        i = index.get(shot.id)
        if i is None:
            out.append(
                f"{where}: no shot {shot.id} in the timeline, so its pageText is never shown; "
                "give every shot its own id and compile again"
            )
            continue
        if (timeline.shots[i].page_text or "").strip() != text:
            out.append(
                f"{where}: the timeline shot does not carry this pageText, so the page shows something else; "
                "keep pageText on the shot and compile again"
            )
            continue
        begin, end, why = _page_text_window(i, timeline)
        if end - begin <= _EPS:
            out.append(
                f"{where}: pageText is never on the page while the shot plays ({why}); "
                "give the shot action or narration that takes time, and keep shots in order"
            )
    return out


# ---------------------------------------------------------------------- narration


def _bare(word: str) -> str:
    return word.strip(".,!?;:\"'()…“”‘’—-").lower()


def _cue_time(line: NarrationLine) -> float:
    """When the cued word is spoken: its word timing if TTS gave one, else the line's start."""
    if not line.words:
        return line.t
    spoken = line.text.split()
    want = _bare(line.cue_word or (spoken[-1] if spoken else ""))
    for w in line.words:
        if _bare(w.word) == want:
            return w.t
    return line.t


def _narration_places(screenplay: Screenplay) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """Where each narration text sits in the screenplay, as review_scene names it: the default
    telling (text -> places) and each other telling (author -> text -> places)."""
    default: dict[str, list[str]] = {}
    for shot in screenplay.shots:
        for i, line in enumerate(shot.narration, start=1):
            default.setdefault(line.text, []).append(f"shot {shot.id}, narration line {i}")
    others: dict[str, dict[str, list[str]]] = {}
    for author, shots in (screenplay.narrations or {}).items():
        for shot_id, lines in shots.items():
            for i, line in enumerate(lines, start=1):
                others.setdefault(author, {}).setdefault(line.text, []).append(
                    f"narrations {author}, shot {shot_id}, line {i}"
                )
    return default, others


def _review_timeline_narration(screenplay: Screenplay, timeline: Timeline) -> list[str]:
    default, others = _narration_places(screenplay)
    out = []
    for author, track in (timeline.narrations or {}).items():
        mine = others.get(author, {})
        for i, line in enumerate(track.lines, start=1):
            places = mine.get(line.text) or default.get(line.text)
            where = places.pop(0) if places else f"narration {author}, line {i}"
            if line.cue:
                when, what = _cue_time(line), f'cue "{line.cue}" lands'
            else:
                when, what = line.t, "starts"
            if not _outside(when, timeline.duration):
                continue
            if when < 0:
                problem = f"{what} at {_num(when)}s, before the timeline starts"
                fix = "start the line at 0 or later in its shot"
            else:
                problem = f"{what} at {_num(when)}s, after the timeline ends at {_num(timeline.duration)}s"
                fix = "start the line earlier in its shot or shorten it, so it is spoken while the shot plays"
            out.append(f"{where}: {problem}; {fix}")
    return out


# ------------------------------------------------------------------- timeline entry


def review_timeline(
    screenplay: Screenplay,
    timeline: Timeline,
    *,
    props: Mapping[str, schemas.Prop] | None = None,
) -> list[str]:
    """Warnings about the compiled timeline, one line each: '<where>: <problem>; <fix>'.

    Checks what the stage will play: pull-tab scrub ranges (timeline time) that are empty or
    fall outside [0, duration], or scrub targets with no track; explanations whose puppet or
    prop has no track; shots whose pageText is never on the page while the shot plays; and
    narration lines whose cue (the cue word's timing when TTS gave one, else the line start)
    lands outside the timeline. `props` is accepted for symmetry with `review_scene`; no
    timeline check needs the rigs yet.
    """
    del props  # reserved: no timeline check needs the rigs yet
    shots = {}
    for ts in timeline.shots:
        shots.setdefault(ts.id, ts)
    targets = {track.target for track in timeline.tracks}
    return (
        _review_timeline_page_text(screenplay, timeline)
        + _review_timeline_explanations(screenplay, timeline, targets)
        + _review_timeline_interactions(timeline, shots, targets)
        + _review_timeline_narration(screenplay, timeline)
    )

