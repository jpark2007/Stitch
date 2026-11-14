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
"""

from __future__ import annotations

import re

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


class NarrationError(Exception):
    """A narration problem the compiler turns into a CompileError.

    ``code`` is a stable id; ``reason`` is a human sentence without the location prefix.
    ``author`` is the teller (None when not known at the raise site), ``shot`` the shot id
    and ``line_index`` the index of the offending line in that teller's lines for the shot
    (for interactions: the index in ``Screenplay.interactions``; -1 when not tied to a line).
    """

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        author: str | None = None,
        shot: str = "",
        line_index: int = -1,
    ) -> None:
        self.code = code
        self.reason = reason
        self.author = author
        self.shot = shot
        self.line_index = line_index
        super().__init__(self.message)

    @property
    def message(self) -> str:
        where = f"shot {self.shot}" if self.shot else "screenplay"
        if self.line_index >= 0:
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
        "cannot tell who narrates: no narration source names a teller and the cast is empty",
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
    known = f"; action ids in this shot: {', '.join(repr(i) for i in ids)}" if ids else ""
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
    tokens = ", ".join(f'"{w.word}"' for w in words)
    raise NarrationError(
        "cue_word_missing",
        f'cueWord "{line.cue_word}" is not a word of "{line.text}"; its words are: {tokens}',
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
    """
    original = [a.t for a in shot.actions]
    current = list(original)
    pinned: dict[int, int] = {}  # action index -> line index that placed it
    for li, line in enumerate(shot.narration):
        if line.cue is None:
            continue
        idx = cue_action_index(shot, line.cue, author=author, line_index=li)
        if idx in pinned:
            a = shot.actions[idx]
            raise NarrationError(
                "cue_conflict",
                f'action {idx} ({a.id or a.do}) is already cued by narration line {pinned[idx]}; '
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
                f'cue word of "{line.text}" is at shot t={new_t:g}, before the shot starts; '
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
) -> dict[str, list[NarrationLine]]:
    """One shot's narration in timeline time: ``author -> lines``, default teller first.

    Lines keep every field of the screenplay line; ``t`` becomes timeline time and ``words``
    is filled in (timeline time). The default telling's lines start where they were written
    (the actions moved to them in ``retime_actions``); each cued line of another telling is
    shifted so its cue word lands on the retimed action. ``other`` is author -> this shot's
    lines from ``Screenplay.narrations``.
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
                        f"action {idx} is already cued by {author}'s narration line {cued_by[idx]}; "
                        f"an action can land on only one word per telling",
                        author=author,
                        shot=shot.id,
                        line_index=li,
                    )
                cued_by[idx] = li
                words = line_words(line, start)
                word_t = cue_word_time(line, words, author=author, shot=shot.id, line_index=li)
                start = start + (action_start + retimed[idx] - word_t)
                if _r(start) < _r(action_start):
                    raise NarrationError(
                        "cue_before_start",
                        f'to land its cue word on the action, "{line.text}" would start at shot '
                        f"t={_r(start - action_start):g}, before the shot starts; "
                        f"shorten the words before the cue word or move the action later",
                        author=author,
                        shot=shot.id,
                        line_index=li,
                    )
            built.append(_timeline_line(line, start))
        out[author] = built
    return out


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

def convert_interactions(
    interactions: list[Interaction],
    action_start_by_shot: dict[str, float],
    first_shot: str,
) -> list[Interaction]:
    """Screenplay interactions -> timeline interactions.

    A ``scrub`` drive's ``from``/``to`` move from shot time to timeline time using the
    shot's ``action_start`` (``shot`` None means the first shot). Every other field is
    copied unchanged.
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
                f'interaction "{it.id}" belongs to shot "{shot_id}", which is not in the screenplay',
                shot=shot_id,
                line_index=i,
            )
        base = action_start_by_shot[shot_id]
        update: dict[str, float | None] = {}
        if it.drives.from_ is not None:
            update["from_"] = _r(base + it.drives.from_)
        if it.drives.to is not None:
            update["to"] = _r(base + it.drives.to)
        drives = it.drives.model_copy(deep=True, update=update)
        out.append(it.model_copy(deep=True, update={"drives": drives}))
    return out
