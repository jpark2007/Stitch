"""Camera cues follow their cued actions: the agent-side carry the real compiler does not do.

`screenplay/compiler.py` moves every action the default telling cues onto its cue word
(`screenplay.narration.retime_actions`, Decision 7) and carries the uncued actions after it by
the same amount, and maps pull-tab scrub ranges with `retime_point`. It leaves `Shot.camera`
cues at their written times. With real (or FakeTTS) word timings a cue word rarely falls
exactly where the director wrote the action, so a `pan_to` written to frame a car as it drives
off would frame it seconds before it moves.

`carry_camera_cues(screenplay)` returns a copy whose camera cues are retimed the way the
compiler retimes scrub ranges: each cue keeps its offset from the action written at or just
before it (`retime_point`, ties to the later action). A cue written at an action's `t` therefore
lands on that action's compiled start. The opening framing (a cue at t=0) is never moved.

`agent.tools.compile._RealCompilerAdapter` compiles the carried copy, so every compile path
(the director's compile tool, the voice pass recompile, save-time compiles) gets it, while the
`/screenplay.json` the director wrote keeps its written camera times. A recompile with new word
timings then maps from the written times again and never shifts a cue twice.

A narration problem (`NarrationError`) leaves the screenplay as it is: the compiler reports
it as a compile error itself.

If `screenplay/compiler.py` ever retimes camera cues itself, delete this carry (the guard test
in tests/agent/test_agent_cue_camera.py fails to say so).
"""

from __future__ import annotations

from screenplay import narration as screenplay_narration
from screenplay.schema import Screenplay

NOTE_MIN_SECONDS = 0.05
"""A cue moved by more than this many seconds gets a note in the compile tool's result."""


def _num(x: float) -> str:
    """Numbers as scene_review._num prints them."""
    return f"{x:g}"


def carry_camera_cues(screenplay: Screenplay) -> tuple[Screenplay, list[str]]:
    """A deep copy of `screenplay` whose camera cues follow the actions the narration moves,
    and one note per cue moved more than NOTE_MIN_SECONDS:

        note: shot <shotId> camera cue <n> (<move>) moves with its action from t=<a> to t=<b>

    `n` is the cue's 1-based place in the shot's `camera` list. The input is never mutated.
    On a NarrationError (bad cue, cue before the shot starts, no teller) the input itself is
    returned with no notes.
    """
    try:
        author = screenplay_narration.default_author(screenplay)
        plans: list[list[float | None]] = []
        for shot in screenplay.shots:
            # Exactly the compiler's call: shot time is measured from the shot's actionStart,
            # so passing 0.0 as action_start gives the same shot-time t the compiler uses.
            original = [a.t for a in shot.actions]
            retimed = screenplay_narration.retime_actions(shot, 0.0, author=author)
            plans.append([
                None if cue.t == 0 else screenplay_narration.retime_point(cue.t, original, retimed)
                for cue in shot.camera
            ])
    except screenplay_narration.NarrationError:
        return screenplay, []

    out = screenplay.model_copy(deep=True)
    notes: list[str] = []
    for shot, plan in zip(out.shots, plans, strict=True):
        for n, (cue, new_t) in enumerate(zip(shot.camera, plan, strict=True), start=1):
            if new_t is None or new_t == cue.t:
                continue
            if abs(new_t - cue.t) > NOTE_MIN_SECONDS:
                notes.append(
                    f"note: shot {shot.id} camera cue {n} ({cue.move}) moves with its action "
                    f"from t={_num(cue.t)} to t={_num(round(new_t, 2))}"
                )
            cue.t = new_t
    return out, notes
