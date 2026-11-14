"""Narration timing: word timings, cue resolution, action retiming, tellings, captions.

ARCHITECTURE.md section 3, compiler step 5 ("Time to narration"), and shared/CONTRACTS.md
('Narration', 'Interactions', 'Screenplay' cue rules, Decision 7: cues land on the start
of the cued action).

Everything here is pure and deterministic. Times are rounded to microseconds so float
noise (4.45 - 1.2 = 3.2500000000000004) never leaks into the timeline.

Time bases:
- shot time: seconds from a shot's screenplay t=0 (``Action.t``, screenplay ``NarrationLine.t``).
- timeline time: seconds from the start of the timeline. ``action_start`` is the timeline
  time of a shot's t=0, so ``timeline = action_start + shot``.

The default telling (``Shot.narration``) drives the action: each cued line moves its action
so the action starts on the cue word. Other tellings (``Screenplay.narrations``) share that
action, so each of their cued lines is shifted instead, so its cue word lands on the action.

Word timings: in the screenplay, ``NarrationLine.words[i].t`` is seconds from the start of
that line (TTS timestamps relative to the line's audio clip). In the timeline every ``t`` is
timeline time: the line's ``t`` and each word's ``t`` are absolute. Lines without ``words`` get
one word per token every ``WORD_SECONDS``.

Speaking rules checked here (each a ``NarrationError``):
- a cue word must land at or after the shot's t=0, and no action may be pushed before it
  (``cue_before_start``);
- a non-default telling's line shifted onto its action must not start before the shot starts,
  that is before its set starts rising at ``TimelineShot.start``, ``layout.RISE`` seconds
  before t=0 (``cue_before_start``);
- within one telling of a shot, a line must not start before the previous line's last word
  has been said, ``WORD_SECONDS`` after its time (``lines_overlap``). ``build_shot_narration``
  checks each other telling right after shifting it, then the default telling once.

Scrub interactions (``convert_interactions``) are written in shot time against the actions as
written. When the compiler passes each shot's original and retimed action times, a scrub's
``from``/``to`` follow those actions onto their cue words (``retime_point``); a range that
would run backwards afterwards is ``interaction_bad_range``. A scrub that leaves out ``from``
starts at its shot's t=0; one that leaves out ``to`` ends where its shot ends, which the
compiler passes as ``end_by_shot`` (without it, a missing ``to`` is ``interaction_missing_to``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from screenplay.layout import RISE
from screenplay.state import fmt_t
from screenplay.schema import (
    Caption,
    Interaction,
    NarrationLine,
    NarrationTrack,
    Screenplay,
    Shot,
    WordTiming,
)

WORD_SECONDS = 0.25
"""Seconds per word when a line has no TTS word timings."""

_DIGITS = 6


def _r(x: float) -> float:
    """Round a time to microseconds, normalising -0.0 to 0.0."""
    return round(x, _DIGITS) + 0.0


def _action_name(shot: Shot, idx: int) -> str:
    a = shot.actions[idx]
    return a.id or a.do


class NarrationError(Exception):
    """A narration problem the compiler turns into a CompileError.

    ``code`` is a stable id; ``reason`` is a human sentence without the location prefix.
    ``author`` is the teller (None when not known at the raise site), ``shot`` the shot id
    and ``line_index`` the index of the offending line in that teller's lines for the shot
    (for interactions: the index in ``Screenplay.interactions``; -1 when not tied to a line).

    The message is ``shot <id>, <author>'s narration line <i>: <reason>``, or ``shot <id>:
    <reason>`` (``screenplay: <reason>`` without a shot) when the error is not tied to a line.
    An interaction error (``interaction=True``) is placed on the interaction, not on a
    narration line: ``shot <id>, interaction <i>: <reason>``.
    """

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        author: str | None = None,
        shot: str = "",
        line_index: int = -1,
        interaction: bool = False,
    ) -> None:
        self.code = code
        self.reason = reason
        self.author = author
        self.shot = shot
        self.line_index = line_index
        self.interaction = interaction
        super().__init__(self.message)

    @property
    def message(self) -> str:
        where = f"shot {self.shot}" if self.shot else "screenplay"
        if self.line_index >= 0 and self.interaction:
            where += f", interaction {self.line_index}"
        elif self.line_index >= 0:
            who = f"{self.author}'s " if self.author else ""
            where += f", {who}narration line {self.line_index}"
        return f"{where}: {self.reason}"


# --------------------------------------------------------------------------- words

def _is_word_char(c: str) -> bool:
    return c.isalnum()


def _strip_token(tok: str) -> str:
    """Strip leading and trailing punctuation, quotes, brackets, dashes and symbols."""
    start, end = 0, len(tok)
    while start < end and not _is_word_char(tok[start]):
        start += 1
    while end > start and not _is_word_char(tok[end - 1]):
        end -= 1
    return tok[start:end]


def tokenize(text: str) -> list[str]:
    """Split narration into spoken words.

    Splits on whitespace, strips leading and trailing punctuation (ellipses, periods,
    commas, quotes, brackets, dashes, ...) and drops tokens that end up empty. Inner
    punctuation such as the apostrophe in "wouldn't" is kept. Case is kept.
    """
    out: list[str] = []
    for raw in text.split():
        tok = _strip_token(raw)
        if tok:
            out.append(tok)
    return out


def _norm(word: str) -> str:
    return _strip_token(word).casefold()


def line_words(line: NarrationLine, line_start: float) -> list[WordTiming]:
    """Word timings for one line, in timeline time.

    If ``line.words`` is given (screenplay convention: each ``t`` is seconds from the line's
    start), word i is at ``line_start + words[i].t``. Otherwise token i of ``line.text`` is
    at ``line_start + WORD_SECONDS * i``.
    """
    if line.words is not None:
        return [WordTiming(t=_r(line_start + w.t), word=w.word) for w in line.words]
    return [WordTiming(t=_r(line_start + WORD_SECONDS * i), word=w) for i, w in enumerate(tokenize(line.text))]


# --------------------------------------------------------------------------- cues

_TELLING = re.compile(r"^telling:([^:]+):")


def default_author(screenplay: Screenplay) -> str:
    """Who tells the default narration.

    The ``<author>`` of the first ``telling:<author>:...`` source among the shots' default
    narration lines, else the first cast member.
    """
    for shot in screenplay.shots:
        for line in shot.narration:
            if line.source:
                m = _TELLING.match(line.source)
                if m:
                    return m.group(1)
    if screenplay.cast:
        return screenplay.cast[0]
    raise NarrationError(
        "no_narrator",
        "nobody narrates: no narration source names a teller and the cast is empty; add someone to the cast",
    )


def cue_action_index(
    shot: Shot,
    cue: str,
    *,
    author: str | None = None,
    line_index: int = -1,
) -> int:
    """Index of the action a cue names: the action whose ``id`` is ``cue``, else the first
    action whose ``do`` is ``cue``."""
    for i, a in enumerate(shot.actions):
        if a.id is not None and a.id == cue:
            return i
    for i, a in enumerate(shot.actions):
        if a.do == cue:
            return i
    ids = [a.id for a in shot.actions if a.id]
    verbs = list(dict.fromkeys(a.do for a in shot.actions))
    known = f"; the action ids in this shot are {', '.join(ids)}" if ids else ""
    known += f"; its verbs are {', '.join(verbs)}" if verbs else "; this shot has no actions"
    raise NarrationError(
        "cue_unknown_action",
        f'cue "{cue}" matches no action id and no action verb in this shot{known}',
        author=author,
        shot=shot.id,
        line_index=line_index,
    )


def cue_word_time(
    line: NarrationLine,
    words: list[WordTiming],
    *,
    author: str | None = None,
    shot: str = "",
    line_index: int = -1,
) -> float:
    """Timeline time of the line's cue word: the first case-insensitive match of
    ``line.cue_word``, or the line's last word when ``cue_word`` is None."""
    if line.cue_word is None:
        if words:
            return words[-1].t
        raise NarrationError(
            "cue_word_missing",
            f'cued line "{line.text}" has no words to cue on',
            author=author,
            shot=shot,
            line_index=line_index,
        )
    target = _norm(line.cue_word)
    for w in words:
        if _norm(w.word) == target:
            return w.t
    tokens = ", ".join(w.word for w in words)
    raise NarrationError(
        "cue_word_missing",
        f'cueWord "{line.cue_word}" is not a word of "{line.text}"; its words are {tokens}',
        author=author,
        shot=shot,
        line_index=line_index,
    )


# ------------------------------------------------------------------------ retiming

def retime_actions(shot: Shot, action_start: float, *, author: str | None = None) -> list[float]:
    """New shot-time ``t`` for each action, so every action cued by the default telling
    starts on its cue word (Decision 7).

    Cued lines are processed in line order. For each, the cued action moves to
    ``cue_word_time - action_start`` and every action whose original ``t`` is later than the
    cued action's original ``t`` and that has not itself been placed by a cue yet moves by
    the same delta (deltas accumulate). An action that is cued later still lands exactly on
    its own word, so uncued actions after it keep their spacing from it.

    Raises ``cue_before_start`` when a cue word, or an uncued action carried earlier by the
    ripples, would land before the shot's t=0. An uncued action written with a negative ``t``
    is returned as written unless a ripple moves it earlier still.
    """
    original = [a.t for a in shot.actions]
    current = list(original)
    pinned: dict[int, int] = {}  # action index -> line index that placed it
    moved_by: dict[int, tuple[int, int]] = {}  # uncued action -> (line, cued action) of its last ripple
    for li, line in enumerate(shot.narration):
        if line.cue is None:
            continue
        idx = cue_action_index(shot, line.cue, author=author, line_index=li)
        if idx in pinned:
            a = shot.actions[idx]
            raise NarrationError(
                "cue_conflict",
                f"action {idx} ({_action_name(shot, idx)}) is already cued by narration line {pinned[idx]}; "
                f"an action can land on only one word",
                author=author,
                shot=shot.id,
                line_index=li,
            )
        words = line_words(line, action_start + line.t)
        word_t = cue_word_time(line, words, author=author, shot=shot.id, line_index=li)
        new_t = _r(word_t - action_start)
        if new_t < 0:
            raise NarrationError(
                "cue_before_start",
                f'cue word of "{line.text}" is at shot t={fmt_t(new_t)}, before the shot starts; '
                f"start the line later",
                author=author,
                shot=shot.id,
                line_index=li,
            )
        delta = new_t - current[idx]
        current[idx] = new_t
        pinned[idx] = li
        if delta:
            for j, t0 in enumerate(original):
                if j not in pinned and t0 > original[idx]:
                    current[j] = _r(current[j] + delta)
                    moved_by[j] = (li, idx)
    # Ripples can push an uncued action before t=0: when a later line cues an earlier action
    # back after an even later action was already pinned, the uncued actions after both get
    # both deltas. Only a ripple that moved an action earlier and below t=0 is a retiming
    # error: an action written with a negative t (no contract forbids it here; validation
    # owns that rule) passes through unchanged when no ripple moves it earlier.
    for j, t_new in enumerate(current):
        if j in moved_by and t_new < 0 and t_new < original[j]:
            li, idx = moved_by[j]
            raise NarrationError(
                "cue_before_start",
                f"action {j} ({_action_name(shot, j)}) would start at shot t={fmt_t(t_new)}, before the shot "
                f"starts: it keeps its spacing after action {idx} ({_action_name(shot, idx)}), which this "
                f"line moves to shot t={fmt_t(current[idx])}; give action {j} a later t or a cue of its own",
                author=author,
                shot=shot.id,
                line_index=li,
            )
    return [_r(t) for t in current]


def align_line_start(line: NarrationLine, line_start: float, action_time_timeline: float) -> float:
    """Start time for a non-default telling's line so its cue word lands on the action.

    ``line_start`` is the line's unshifted timeline start and ``action_time_timeline`` the
    (retimed) timeline time of the cued action. Uncued lines are unchanged.
    """
    if line.cue is None:
        return _r(line_start)
    word_t = cue_word_time(line, line_words(line, line_start))
    return _r(line_start + (action_time_timeline - word_t))


# ------------------------------------------------------------------------- tellings

def _timeline_line(line: NarrationLine, start: float) -> NarrationLine:
    return line.model_copy(update={"t": _r(start), "words": line_words(line, start)})


def build_shot_narration(
    shot: Shot,
    action_start: float,
    retimed: list[float],
    other: dict[str, list[NarrationLine]],
    default: str,
    *,
    check_default: bool = True,
) -> dict[str, list[NarrationLine]]:
    """One shot's narration in timeline time: ``author -> lines``, default teller first.

    Lines keep every field of the screenplay line; ``t`` becomes timeline time and ``words``
    is filled in (timeline time). The default telling's lines start where they were written
    (the actions moved to them in ``retime_actions``); each cued line of another telling is
    shifted so its cue word lands on the retimed action. ``other`` is author -> this shot's
    lines from ``Screenplay.narrations``.

    ``lines_overlap`` is checked for each other teller right after its own cue checks, in
    ``other`` order, and then for the default telling, so a default overlap never hides a
    teller's own error. A caller that builds the tellings one teller per call (validation)
    should pass ``check_default=False`` on those calls and check the default once, with
    ``other={}``, so a default overlap is reported once per shot.
    """
    out: dict[str, list[NarrationLine]] = {
        default: [_timeline_line(line, action_start + line.t) for line in shot.narration]
    }
    for author, lines in other.items():
        if author == default:
            raise NarrationError(
                "duplicate_telling",
                f"{author} tells the default narration (Shot.narration) and also has a telling "
                f"in Screenplay.narrations; give each teller one telling",
                author=author,
                shot=shot.id,
            )
        built: list[NarrationLine] = []
        cued_by: dict[int, int] = {}
        for li, line in enumerate(lines):
            start = action_start + line.t
            if line.cue is not None:
                idx = cue_action_index(shot, line.cue, author=author, line_index=li)
                if idx in cued_by:
                    raise NarrationError(
                        "cue_conflict",
                        f"action {idx} ({_action_name(shot, idx)}) is already cued by {author}'s narration "
                        f"line {cued_by[idx]}; "
                        f"an action can land on only one word per telling",
                        author=author,
                        shot=shot.id,
                        line_index=li,
                    )
                cued_by[idx] = li
                words = line_words(line, start)
                word_t = cue_word_time(line, words, author=author, shot=shot.id, line_index=li)
                start = start + (action_start + retimed[idx] - word_t)
                if _r(start) < _r(action_start - RISE):
                    raise NarrationError(
                        "cue_before_start",
                        f'to land its cue word on the action, "{line.text}" would start at shot '
                        f"t={fmt_t(start - action_start)}, before the shot starts; "
                        f"shorten the words before the cue word or move the action later",
                        author=author,
                        shot=shot.id,
                        line_index=li,
                    )
            built.append(_timeline_line(line, start))
        _check_no_overlap(shot, action_start, author, built)
        out[author] = built
    if check_default:
        _check_no_overlap(shot, action_start, default, out[default])
    return out


def _check_no_overlap(shot: Shot, action_start: float, author: str | None, lines: list[NarrationLine]) -> None:
    """Raise ``lines_overlap`` when a line starts before the previous spoken line has been said.

    ``lines`` are timeline lines (words filled in). A line is said by its last word's time
    plus ``WORD_SECONDS``. Lines with no words are silent and skipped.
    """
    prev: tuple[int, float] | None = None  # (line index, timeline time it has been said by)
    for i, line in enumerate(lines):
        if not line.words:
            continue
        if prev is not None and line.t < prev[1] - 1e-6:
            raise NarrationError(
                "lines_overlap",
                f"line {i} starts at shot t={fmt_t(line.t - action_start)}, before line {prev[0]} has been "
                f"said (its last word ends at shot t={fmt_t(prev[1] - action_start)}); start line {i} "
                f"later or shorten line {prev[0]}",
                author=author,
                shot=shot.id,
                line_index=i,
            )
        prev = (i, _r(max(w.t for w in line.words) + WORD_SECONDS))


def shot_tellings(screenplay: Screenplay, shot_id: str) -> dict[str, list[NarrationLine]]:
    """``author -> lines`` of the other tellings (``Screenplay.narrations``) for one shot.

    Authors keep their ``narrations`` order; an author with no lines for the shot is left out.
    """
    return {
        author: by_shot[shot_id]
        for author, by_shot in (screenplay.narrations or {}).items()
        if by_shot.get(shot_id)
    }


def narrate_shot(
    screenplay: Screenplay,
    shot: Shot,
    action_start: float,
    default: str,
) -> tuple[list[float], dict[str, list[NarrationLine]]]:
    """Retime one shot's actions onto the default telling's cue words, then build every
    telling's narration for the shot in timeline time.

    Returns ``(retimed, lines_by_author)``: ``retimed[i]`` is the new shot-time ``t`` of
    ``shot.actions[i]`` (``retime_actions``) and ``lines_by_author`` is as in
    ``build_shot_narration`` (default teller first, then ``shot_tellings`` order).
    """
    retimed = retime_actions(shot, action_start, author=default)
    built = build_shot_narration(shot, action_start, retimed, shot_tellings(screenplay, shot.id), default)
    return retimed, built


def last_word_time(lines_by_author: dict[str, list[NarrationLine]]) -> float | None:
    """Timeline time of the last spoken word across every telling, or None if no words."""
    times = [w.t for lines in lines_by_author.values() for line in lines for w in (line.words or [])]
    return max(times) if times else None


def assemble(
    per_shot: list[dict[str, list[NarrationLine]]],
    default: str,
) -> tuple[dict[str, NarrationTrack], list[Caption]]:
    """Join per-shot narration into ``Timeline.narrations`` and the top-level captions.

    Authors are keyed in first-seen order with the default teller first (always present).
    Captions are ``{t, text}`` per line. The top-level captions repeat the default's.
    """
    lines_by: dict[str, list[NarrationLine]] = {default: []}
    for shot_lines in per_shot:
        for author, lines in shot_lines.items():
            lines_by.setdefault(author, []).extend(lines)
    tracks = {
        author: NarrationTrack(
            author_id=author,
            audio=None,
            captions=[Caption(t=line.t, text=line.text) for line in lines],
            lines=lines,
        )
        for author, lines in lines_by.items()
    }
    return tracks, [c.model_copy() for c in tracks[default].captions]


# ---------------------------------------------------------------------- interactions

def retime_point(t: float, original: Sequence[float], retimed: Sequence[float]) -> float:
    """Where shot time ``t`` lands once the shot's actions have been retimed.

    ``original[i]`` / ``retimed[i]`` are action i's shot-time ``t`` before and after
    ``retime_actions``. ``t`` follows the action i with the largest ``original[i] <= t`` (ties:
    the largest i) and keeps its offset from it: ``retimed[i] + (t - original[i])``. Before the
    first action, or with no actions, ``t`` is returned unchanged. Raises ``ValueError`` when
    the two sequences differ in length.
    """
    if len(original) != len(retimed):
        raise ValueError(f"retime_point needs one retimed t per action: {len(original)} != {len(retimed)}")
    best: int | None = None
    for i, t0 in enumerate(original):
        if t0 <= t and (best is None or t0 >= original[best]):
            best = i
    if best is None:
        return t
    return _r(retimed[best] + (t - original[best]))


def convert_interactions(
    interactions: list[Interaction],
    action_start_by_shot: dict[str, float],
    first_shot: str,
    *,
    retimed_by_shot: Mapping[str, tuple[Sequence[float], Sequence[float]]] | None = None,
    end_by_shot: Mapping[str, float] | None = None,
) -> list[Interaction]:
    """Screenplay interactions -> timeline interactions.

    A ``scrub`` drive's ``from``/``to`` move from shot time to timeline time using the
    shot's ``action_start`` (``shot`` None means the first shot). Every other field is
    copied unchanged.

    The timeline always gets both ends, so a tab only ever scrubs its own shot:

    - a missing ``from`` is the shot's t=0, its ``action_start`` in timeline time;
    - a missing ``to`` is the shot's end, ``end_by_shot[shot]`` (timeline time). If the shot
      has no entry there (or ``end_by_shot`` is None), ``interaction_missing_to`` is raised.

    ``retimed_by_shot`` maps a shot id to ``(original, retimed)``: its actions' shot-time
    ``t`` before and after ``retime_actions``. When the scrub's shot has an entry, a ``from``
    or ``to`` written in the screenplay first follows the actions onto their cue words
    (``retime_point``), so the tab still scrubs the actions it was written for; a filled-in
    default is never retimed. If the range then runs backwards (``to`` before ``from``),
    ``interaction_bad_range`` is raised. Without ``retimed_by_shot`` (or with no entry for the
    shot) the shot times are used as written.
    """
    out: list[Interaction] = []
    for i, it in enumerate(interactions):
        if it.drives.type != "scrub":
            out.append(it.model_copy(deep=True))
            continue
        shot_id = it.shot if it.shot is not None else first_shot
        if shot_id not in action_start_by_shot:
            raise NarrationError(
                "interaction_unknown_shot",
                f'interaction "{it.id}" belongs to shot "{shot_id}", which is not in the screenplay; '
                f"the shots are {', '.join(action_start_by_shot)}",
                line_index=i,
                interaction=True,
            )
        base = action_start_by_shot[shot_id]
        t_from, t_to = it.drives.from_, it.drives.to
        shot_end = end_by_shot.get(shot_id) if end_by_shot is not None else None
        if t_to is None and shot_end is None:
            raise NarrationError(
                "interaction_missing_to",
                f'interaction "{it.id}" needs "to", the shot time its scrub ends',
                shot=shot_id,
                line_index=i,
                interaction=True,
            )
        timing = retimed_by_shot.get(shot_id) if retimed_by_shot is not None else None
        if timing is not None:
            original, retimed = timing
            if t_from is not None:
                t_from = retime_point(t_from, original, retimed)
            if t_to is not None:
                t_to = retime_point(t_to, original, retimed)
            if t_from is not None and t_to is not None and t_to < t_from:
                raise NarrationError(
                    "interaction_bad_range",
                    f'interaction "{it.id}" would scrub from shot t={fmt_t(t_from)} back to t={fmt_t(t_to)} '
                    f"once its actions move onto their cue words; widen the range or cue the actions in order",
                    shot=shot_id,
                    line_index=i,
                    interaction=True,
                )
        tl_from = _r(base + t_from) if t_from is not None else _r(base)
        if t_to is not None:
            tl_to = _r(base + t_to)
        else:
            assert shot_end is not None  # a missing to without an end was raised above
            tl_to = _r(shot_end)
        if (t_from is None or t_to is None) and tl_to < tl_from:
            # A filled-in end on the wrong side of the written one (two written ends are
            # checked by validate, and above once retimed).
            if t_to is None:
                reason = (
                    f'interaction "{it.id}" would scrub from shot t={fmt_t(tl_from - base)} back to '
                    f't={fmt_t(tl_to - base)}, where shot "{shot_id}" ends; give it a "to" after its "from"'
                )
            else:
                reason = (
                    f'interaction "{it.id}" would scrub from shot t=0, where shot "{shot_id}" starts, '
                    f'back to t={fmt_t(tl_to - base)}; give it a "from" before its "to"'
                )
            raise NarrationError("interaction_bad_range", reason, shot=shot_id, line_index=i, interaction=True)
        drives = it.drives.model_copy(deep=True, update={"from_": tl_from, "to": tl_to})
        out.append(it.model_copy(deep=True, update={"drives": drives}))
    return out
