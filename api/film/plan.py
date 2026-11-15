"""Plan: the film's beats, each a narration line and the photos shown under it.

`ModelEditor` (film.types.Editor) makes ONE text call to the studio's Claude chat model
(agent.studio.make_model) asking for strict JSON. Its reply is checked hard by
`validate_plan`:

- unknown photo ids, and a photo used a second time, are dropped;
- a beat with no narration hands its photos to the beat before it (or after, if first);
  a beat with narration but no photos hands its narration on the same way;
- photos the editor left out are added to the beat whose photos are nearest in capture
  order, at their capture-order place: all of them when no shot count is wanted, else only
  up to the wanted count (best quality first) and never one that repeats a used photo's
  scene (see below);
- captions are cut to MAX_CAPTION_WORDS words; a missing one is the photo's description;
- more than MAX_EDITOR_BEATS beats, or narration far past the film's length, is rejected.

Anything malformed, an editor that raises, or no editor at all gives `fallback_plan`:
photos in chronological order, the story's sentences (trimmed to MAX_NARRATION_WORDS by
`trim_story`) spread over evenly sized beats, captions from the descriptions.

The film is described with a few more photos than it wants shots (selection.candidate_count)
so repeats can be left out. With a wanted shot count, both plans keep at most that many
photos (`trim_to`), dropping first a photo whose scene label (`scene_key` of the describer's
"scene") equals that of an earlier kept photo taken within SAME_SCENE_SECONDS (or when either
time is unknown), then the lowest-quality photos (never the film's first or last photo while
another can go). Trimming never takes a beat's only photo.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from film.describe import extract_json_object, short_caption
from film.selection import MAX_NARRATION_WORDS, target_count, word_count
from film.types import Editor, Story

log = logging.getLogger(__name__)

SAME_SCENE_SECONDS = 600.0
"""Two photos with the same scene label this close in time (or with unknown times) are one moment."""
_ARTICLES = frozenset({"a", "an", "the"})

MIN_BEATS = 3
MAX_BEATS = 6
MAX_EDITOR_BEATS = 8
"""The prompt asks for 3 to 6 beats; up to this many are accepted, more is a malformed plan."""
WORDS_PER_BEAT = 45
EDITOR_WORD_SLACK = 1.25
"""An editor's narration may run this much past MAX_NARRATION_WORDS before it is rejected."""
TITLE_WORDS = 5

_SENTENCE_END = ".!?…"
_CLOSERS = "\"'’”)]}»"


@dataclass
class PlanShot:
    photo_id: str
    caption: str | None


@dataclass
class PlanBeat:
    title: str
    narration: str
    shots: list[PlanShot] = field(default_factory=list)


# ------------------------------------------------------------------ text helpers


def sentences(text: str) -> list[str]:
    """The story's sentences: a sentence ends at a word ending in . ! ? or an ellipsis."""
    out: list[str] = []
    current: list[str] = []
    for token in text.split():
        current.append(token)
        tail = token.rstrip(_CLOSERS)
        if tail and (tail[-1] in _SENTENCE_END or tail.endswith("...")):
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def trim_story(text: str, max_words: int = MAX_NARRATION_WORDS) -> list[str]:
    """The sentences the film narrates: all of them when they fit in `max_words`, else the
    opening sentences that fit plus the closing one (endings matter); one over-long sentence
    is cut to `max_words` words."""
    all_sentences = sentences(text)
    if sum(word_count(s) for s in all_sentences) <= max_words:
        return all_sentences
    last = all_sentences[-1]
    budget = max_words - word_count(last)
    kept: list[str] = []
    for s in all_sentences[:-1]:
        n = word_count(s)
        if n > budget:
            break
        kept.append(s)
        budget -= n
    if not kept:
        # The ending alone is too long, or the opening sentence is: narrate the start, cut.
        return [" ".join(text.split()[:max_words])]
    return kept + [last]


def title_from(narration: str, max_words: int = TITLE_WORDS) -> str:
    """A title from the opening words of a narration line."""
    words = narration.split()[:max_words]
    return " ".join(words).rstrip(" ,;:-—.!?…") or "…"


def _clean(value: Any, limit: int = 2000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def cut_points(weights: list[float], parts: int) -> list[int]:
    """Indexes splitting `weights` into `parts` contiguous non-empty runs of near-equal weight.

    Returns parts + 1 boundaries starting at 0 and ending at len(weights).
    """
    m = len(weights)
    parts = max(1, min(parts, m))
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + w)
    total = cum[-1]
    bounds = [0]
    for k in range(1, parts):
        target = total * k / parts
        lo, hi = bounds[-1] + 1, m - (parts - k)
        bounds.append(min(range(lo, hi + 1), key=lambda j: (abs(cum[j] - target), j)))
    bounds.append(m)
    return bounds


def split_counts(total: int, weights: list[float]) -> list[int]:
    """`total` items over len(weights) groups in proportion to the weights, each at least 1."""
    n = len(weights)
    if total < n:
        raise ValueError("fewer items than groups")
    whole = sum(weights) or 1.0
    counts = [1] * n
    spare = total - n
    shares = [spare * w / whole for w in weights]
    for i, share in enumerate(shares):
        counts[i] += int(share)
    left = total - sum(counts)
    order = sorted(range(n), key=lambda i: (-(shares[i] - int(shares[i])), i))
    for i in order[:left]:
        counts[i] += 1
    return counts


# ------------------------------------------------------------------ leaving repeats out


def scene_key(value: Any) -> str:
    """A describer's scene label made comparable: lower case, words only, no articles."""
    if not isinstance(value, str):
        return ""
    return " ".join(w for w in re.findall(r"[a-z0-9]+", value.lower()) if w not in _ARTICLES)


def _taken(photo: dict) -> datetime | None:
    raw = photo.get("takenAt")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def same_moment(a: dict, b: dict) -> bool:
    """True when two described photos carry the same scene label and were taken within
    SAME_SCENE_SECONDS of each other (or either time is unknown)."""
    key = scene_key(a.get("scene"))
    if not key or key != scene_key(b.get("scene")):
        return False
    ta, tb = _taken(a), _taken(b)
    if ta is None or tb is None:
        return True
    try:
        return abs((ta - tb).total_seconds()) <= SAME_SCENE_SECONDS
    except TypeError:  # one aware, one naive: unknown, so treated as close
        return True


def _quality(photo: dict) -> float:
    q = photo.get("quality", 0.5)
    return float(q) if isinstance(q, (int, float)) and not isinstance(q, bool) else 0.5


def scene_repeats(photos: list[dict]) -> list[str]:
    """Ids of the photos (in the given capture order) that repeat the moment of an earlier
    photo that is not itself a repeat."""
    kept: list[dict] = []
    repeats: list[str] = []
    for p in photos:
        if any(same_moment(k, p) for k in kept):
            repeats.append(p["id"])
        else:
            kept.append(p)
    return repeats


def trim_to(beats: list[PlanBeat], photos: list[dict], wanted: int) -> None:
    """Drop shots (in place) until at most `wanted` remain (see the module docstring).

    `photos` are the described photos in capture order. A beat's only photo is never taken.
    """
    index = {p["id"]: i for i, p in enumerate(photos)}
    by_id = {p["id"]: p for p in photos}

    def total() -> int:
        return sum(len(b.shots) for b in beats)

    def beat_of(pid: str) -> PlanBeat | None:
        return next((b for b in beats if any(s.photo_id == pid for s in b.shots)), None)

    def drop(pid: str) -> bool:
        beat = beat_of(pid)
        if beat is None or len(beat.shots) < 2:
            return False
        beat.shots = [s for s in beat.shots if s.photo_id != pid]
        return True

    if total() <= wanted:
        return
    shown = sorted({s.photo_id for b in beats for s in b.shots}, key=lambda pid: index[pid])
    for pid in reversed(scene_repeats([by_id[i] for i in shown])):
        if total() <= wanted:
            return
        if drop(pid):
            log.info("film plan: leaving out %s, the same moment as an earlier photo", pid)
    ends = {shown[0], shown[-1]}
    while total() > wanted:
        movable = [(b, s.photo_id) for b in beats if len(b.shots) > 1 for s in b.shots]
        if not movable:
            return
        inner = [m for m in movable if m[1] not in ends] or movable
        beat, pid = min(inner, key=lambda m: (_quality(by_id[m[1]]), -len(m[0].shots), -index[m[1]]))
        drop(pid)


def merge_empty_beats(beats: list[PlanBeat]) -> list[PlanBeat]:
    """Beats without photos hand their narration to the beat before them (or after, if
    first), the same rule validate_plan applies to an editor's beat with no photos."""
    out: list[PlanBeat] = []
    pending: list[str] = []
    for b in beats:
        if not b.shots:
            if out:
                out[-1].narration = f"{out[-1].narration} {b.narration}".strip()
            else:
                pending.append(b.narration)
            continue
        if pending:
            b.narration = " ".join([*pending, b.narration]).strip()
            pending = []
        out.append(b)
    return out


# ------------------------------------------------------------------ fallback


def beat_count(words: int, n_sentences: int, n_photos: int) -> int:
    want = max(MIN_BEATS, round(words / WORDS_PER_BEAT))
    return max(1, min(MAX_BEATS, want, n_sentences, n_photos))


def fallback_plan(story: Story, photos: list[dict], wanted: int | None = None) -> list[PlanBeat]:
    """Chronological photos under the story's sentences, spread over evenly sized beats.
    With `wanted`, at most that many photos (trim_to's order of leaving out)."""
    if wanted is not None and len(photos) > wanted:
        whole = [PlanBeat("", "", [PlanShot(p["id"], None) for p in photos])]
        trim_to(whole, photos, max(1, wanted))
        keep = {s.photo_id for s in whole[0].shots}
        photos = [p for p in photos if p["id"] in keep]
    lines = trim_story(story.text)
    words = [max(1, word_count(s)) for s in lines]
    n = beat_count(sum(words), len(lines), len(photos))
    bounds = cut_points([float(w) for w in words], n)
    narrations = [" ".join(lines[bounds[k] : bounds[k + 1]]) for k in range(n)]
    counts = split_counts(len(photos), [float(word_count(t)) for t in narrations])
    beats: list[PlanBeat] = []
    at = 0
    for narration, count in zip(narrations, counts, strict=True):
        shots = [PlanShot(p["id"], short_caption(p.get("caption"))) for p in photos[at : at + count]]
        at += count
        beats.append(PlanBeat(title=title_from(narration), narration=narration, shots=shots))
    return beats


# ------------------------------------------------------------------ validating an editor's plan


def _photo_id(shot: dict) -> Any:
    for key in ("photoId", "photo_id", "id"):
        if key in shot:
            return shot[key]
    return None


def validate_plan(raw: Any, photos: list[dict], wanted: int | None = None) -> list[PlanBeat] | None:
    """The editor's plan made safe (see the module docstring), or None when it is unusable.
    `wanted` is the film's shot count; None keeps every photo."""
    if not isinstance(raw, dict) or not isinstance(raw.get("beats"), list):
        return None
    if not raw["beats"] or len(raw["beats"]) > MAX_EDITOR_BEATS:
        return None
    index = {p["id"]: i for i, p in enumerate(photos)}
    described = {p["id"]: short_caption(p.get("caption")) for p in photos}
    used: set[str] = set()
    out: list[PlanBeat] = []
    pending_narration: list[str] = []
    pending_shots: list[PlanShot] = []
    for beat in raw["beats"]:
        if not isinstance(beat, dict):
            continue
        narration = _clean(beat.get("narration"), 20_000)
        shots: list[PlanShot] = []
        for shot in beat.get("shots") if isinstance(beat.get("shots"), list) else []:
            if not isinstance(shot, dict):
                continue
            pid = _photo_id(shot)
            if not isinstance(pid, str) or pid not in index or pid in used:
                continue
            used.add(pid)
            caption = short_caption(_clean(shot.get("caption"), 200)) or described[pid]
            shots.append(PlanShot(pid, caption))
        if not narration and not shots:
            continue
        if not narration:
            (out[-1].shots if out else pending_shots).extend(shots)
            continue
        if not shots:
            if out:
                out[-1].narration = f"{out[-1].narration} {narration}"
            else:
                pending_narration.append(narration)
            continue
        title = _clean(beat.get("title"), 80)
        narration = " ".join([*pending_narration, narration])
        out.append(PlanBeat(title=title, narration=narration, shots=pending_shots + shots))
        pending_narration, pending_shots = [], []
    if not out:
        return None
    if sum(word_count(b.narration) for b in out) > MAX_NARRATION_WORDS * EDITOR_WORD_SLACK:
        log.info("film plan: the editor's narration is too long; using the fallback")
        return None
    for b in out:
        if not b.title:
            b.title = title_from(b.narration)
    placed = [[index[s.photo_id] for s in b.shots] for b in out]  # the editor's own choices
    for p in _left_out(photos, used, wanted):
        _add_by_capture(out, placed, PlanShot(p["id"], described[p["id"]]), index)
    if wanted is not None:
        trim_to(out, photos, max(1, wanted))
    return out


def _left_out(photos: list[dict], used: set[str], wanted: int | None) -> list[dict]:
    """The photos the editor left out that go back in: all of them when no count is wanted;
    else, up to the wanted count, the best (then earliest) that do not repeat the moment of a
    photo already in."""
    missing = [p for p in photos if p["id"] not in used]
    if wanted is None:
        return missing
    room = wanted - len(used)
    if room <= 0:
        return []
    inside = [p for p in photos if p["id"] in used]
    back: list[dict] = []
    for p in sorted(missing, key=lambda p: -_quality(p)):  # stable: earliest first on a tie
        if len(back) == room:
            break
        if not any(same_moment(q, p) for q in inside + back):
            back.append(p)
    return back


def _add_by_capture(beats: list[PlanBeat], placed: list[list[int]], shot: PlanShot, index: dict[str, int]) -> None:
    """Put a left-out photo in the beat whose chosen photos are nearest it in capture order,
    at its capture-order place among that beat's photos."""
    at = index[shot.photo_id]
    best = min(range(len(beats)), key=lambda b: (min(abs(i - at) for i in placed[b]), b))
    shots = beats[best].shots
    pos = next((k for k, s in enumerate(shots) if index[s.photo_id] > at), len(shots))
    shots.insert(pos, shot)


# ------------------------------------------------------------------ the model editor

EDITOR_SYSTEM = """\
You are the film editor for Stitch, a family memory book. A family member chose photos of one
event and wrote the memory in their own words. You cut it into a short narrated photo film.

Reply with ONLY a JSON object, no other text:
{"beats": [{"title": "<2 to 5 words>",
            "narration": "<the line spoken over this beat>",
            "shots": [{"photoId": "<id>", "caption": "<at most 8 words>"}]}]}

Rules:
- 3 to 6 beats that follow the story's arc. Every beat has narration and at least one photo.
- Narration is the teller's own words, first person, in their voice: copy their sentences,
  only lightly trimmed (drop asides, repetition and filler). Never invent a fact, a name, a
  feeling or a detail the story does not state. Keep all the narration under 300 words.
- Use every photo at most once, by its exact id. Put each photo under the beat it shows.
  Keep photos in the order they were taken unless the story clearly tells events in another
  order.
- Use about wantedShots photos in all, never more. There are a few spare photos on purpose:
  it is fine, and expected, to leave photos out.
- Never show the same moment twice. When two photos show the same moment, pose or scene (the
  same "scene", or the same people in the same place a few minutes apart, even from another
  angle), use only the better one: the higher "quality", the clearer view of the people.
- A caption is a short handwritten note for the photo, at most 8 words, drawn from the
  story and what the photo shows. Do not guess who people are unless the story says so.
"""


def wanted_shots(story: Story, photos: list[dict]) -> int:
    """The shots the film wants: selection.target_count of the story, at most the photos."""
    return min(len(photos), target_count(story.text))


def editor_request(story: Story, photos: list[dict]) -> str:
    payload = {
        "title": story.title,
        "year": story.year,
        "people": story.people,
        "story": story.text,
        "wantedShots": wanted_shots(story, photos),
        "photos": [
            {
                "id": p["id"],
                "takenAt": p.get("takenAt"),
                "caption": p.get("caption"),
                "scene": p.get("scene") or None,
                "people": p.get("people"),
                "setting": p.get("setting"),
                "mood": p.get("mood"),
                "quality": p.get("quality"),
            }
            for p in photos
        ],
    }
    return (
        "Here is the memory and its photos (in the order they were taken). Plan the film.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )


def reply_text(reply: Any) -> str:
    """The text of a chat model reply: a str, or an AIMessage whose content is a str or blocks."""
    if isinstance(reply, str):
        return reply
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


class ModelEditor:
    """film.types.Editor over a LangChain chat model (agent.studio.make_model in production)."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def plan(self, story: Story, photos: list[dict]) -> dict | None:
        try:
            reply = self.model.invoke([("system", EDITOR_SYSTEM), ("human", editor_request(story, photos))])
            return extract_json_object(reply_text(reply))
        except Exception as exc:  # noqa: BLE001 - any editor failure means the fallback plan
            log.warning("film plan: the editor failed (%s); using the fallback plan", type(exc).__name__)
            return None


def make_plan(story: Story, photos: list[dict], editor: Editor | None, wanted: int | None = None) -> list[PlanBeat]:
    """The editor's plan when it gives a usable one, else the deterministic fallback. With
    `wanted` (the film's shot count), at most that many photos are kept (trim_to)."""
    if editor is not None:
        try:
            raw = editor.plan(story, photos)
        except Exception as exc:  # noqa: BLE001 - an editor is an outside service
            log.warning("film plan: the editor raised (%s); using the fallback plan", type(exc).__name__)
            raw = None
        if raw is not None:
            beats = validate_plan(raw, photos, wanted)
            if beats:
                return beats
            log.info("film plan: the editor's plan was unusable; using the fallback plan")
    return fallback_plan(story, photos, wanted)


__all__ = [
    "EDITOR_SYSTEM",
    "MAX_BEATS",
    "MIN_BEATS",
    "SAME_SCENE_SECONDS",
    "ModelEditor",
    "PlanBeat",
    "PlanShot",
    "cut_points",
    "editor_request",
    "fallback_plan",
    "make_plan",
    "merge_empty_beats",
    "reply_text",
    "same_moment",
    "scene_key",
    "scene_repeats",
    "sentences",
    "split_counts",
    "title_from",
    "trim_story",
    "trim_to",
    "validate_plan",
    "wanted_shots",
]
