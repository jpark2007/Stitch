"""Time and cost guards for one studio run: ``RunLimits``, ``RunUsage`` and ``env_value``.

A run is bounded in these ways, each read from the environment by ``RunLimits.from_env``:

    STITCH_MAX_MODEL_CALLS      chat model calls one run may make in all (default 600, at least
                                100): the studio's and every sub-agent's, across every resume
                                and a restart. The per-run cost cap. Counted by
                                ``ModelCallBudget`` into ``RunUsage``; past it the next call
                                raises ``RunBudgetExceeded`` and the run manager ends the run with
                                one error event.
    STITCH_MAX_IMAGES_PER_RUN   image generations one run may make (default 24). 0 means none:
                                every prop and set layer gets placeholder art (null art + color).
                                Enforced by ``ArtService`` (agent/tools/art.py), per run id,
                                counted in ``RunUsage``, so it holds across a restart too.
    STITCH_MAX_GRAPH_STEPS      steps per studio graph invocation (default 1000, at least 50): the
                                LangGraph recursion limit of each ``graph.stream`` the run manager
                                makes (``studio.thread_config``). It is NOT a per-run cap: a new
                                studio stream starts, with a fresh step budget, on every resume
                                (each answer, each rejected malformed pause, a restart). Nor does
                                it bound a sub-agent: deepagents and langchain bind their own
                                recursion limit (9999) on every sub-agent graph, and the bound
                                config wins over the studio's. It only stops one studio stream
                                that loops. The per-run caps are STITCH_MAX_MODEL_CALLS (cost) and
                                STITCH_RUN_TIMEOUT_S (time).
    STITCH_MODEL_TIMEOUT_S      seconds one Claude request may take (default 120). Used by
                                ``studio.make_model`` (ChatAnthropic ``timeout``).
    STITCH_MODEL_MAX_RETRIES    retries of a failed Claude request (default 2). Used by
                                ``studio.make_model`` (ChatAnthropic ``max_retries``).
    STITCH_RUN_TIMEOUT_S        seconds of running time one run may take in all (default 1200);
                                time spent waiting on the teller's answer does not count.
                                Parsed here; enforced by the run manager (agent/runs.py).
    STITCH_ART_LAYERS           "on" (default) or "off", any case. Off: set layers are never
                                painted by the image model; every layer is a flat placeholder
                                color layer, with no image call and nothing counted against the
                                image budget. Props, parts, states and cut-outs of uploaded photos
                                are unaffected. Enforced by ``ArtService.make_layer``
                                (``layers_enabled``).

A blank, unparsable or out-of-range value (for STITCH_ART_LAYERS: anything but on or off) never
stops the API: it logs one warning naming the
variable and the default is used. The warning is logged once per (variable, value) in a
process, since the limits are read again on every run.

``RunUsage`` is the small per-run usage registry (model calls and images used, by run id) that
both caps count in. The run manager (agent/runs.py) writes both numbers into the run record
with ``elapsed_s`` and seeds them back when it restores a run, so neither cap starts over
after a restart.

``env_value(environ, name)`` is how every model id (and any other optional setting) is read:
the value stripped, or None when it is unset, blank or whitespace, so a blank ``STITCH_MODEL=``
means the default everywhere and a padded one is used stripped (the preflight report reads it
the same way).
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)

ENV_MAX_IMAGES_PER_RUN = "STITCH_MAX_IMAGES_PER_RUN"
ENV_MAX_GRAPH_STEPS = "STITCH_MAX_GRAPH_STEPS"
ENV_MODEL_TIMEOUT_S = "STITCH_MODEL_TIMEOUT_S"
ENV_MODEL_MAX_RETRIES = "STITCH_MODEL_MAX_RETRIES"
ENV_RUN_TIMEOUT_S = "STITCH_RUN_TIMEOUT_S"
ENV_MAX_MODEL_CALLS = "STITCH_MAX_MODEL_CALLS"
ENV_ART_LAYERS = "STITCH_ART_LAYERS"

MIN_GRAPH_STEPS = 50
"""Below this a normal run cannot finish (the studio and its sub-agents take dozens of steps)."""

MIN_MODEL_CALLS = 100
"""Below this a normal run cannot finish. The fake studio's runs (tests/agent: the e2e, story,
fallback and demo runs, eight sub-agents each) make 23 to 57 chat model calls; a real run with
Claude makes roughly 60 to 200 (the studio about 20 turns, each of the eight sub-agents 3 to 20,
more with compile and checker rounds)."""


def env_value(environ: Mapping[str, str] | None, name: str) -> str | None:
    """``environ[name]`` stripped, or None when it is unset, blank or only whitespace.

    ``environ`` None reads ``os.environ``. Every model id the studio reads (STITCH_MODEL,
    STITCH_SUBAGENT_MODEL, STITCH_VISION_MODEL, IMAGE_MODEL, ELEVENLABS_MODEL) goes through
    this, in production and in the preflight report alike, so a blank value means the default
    and a padded one is used stripped in both."""
    env = os.environ if environ is None else environ
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None

_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def _warn_once(name: str, raw: str, rule: str, default: Any) -> None:
    with _warned_lock:
        if (name, raw) in _warned:
            return
        _warned.add((name, raw))
    log.warning("%s=%r is not %s; using the default %s", name, raw, rule, default)


def _parse_int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_switch(raw: str | None) -> bool | None:
    """An on/off switch value: 'on' -> True, 'off' -> False (stripped, any case); else None."""
    if raw is None:
        return None
    return {"on": True, "off": False}.get(raw.strip().lower())


def _read_switch(env: Mapping[str, str], name: str, default: bool) -> bool:
    """An on/off switch from ``env``; unset is ``default``, any other value warns once and keeps it."""
    raw = env.get(name)
    if raw is None:
        return default
    value = parse_switch(raw)
    if value is None:
        _warn_once(name, raw, "'on' or 'off'", "on" if default else "off")
        return default
    return value


@dataclass(frozen=True)
class RunLimits:
    """The time and cost limits of one run (see the module docstring for the env vars)."""

    max_images_per_run: int = 24
    max_graph_steps: int = 1000
    model_timeout_s: float = 120.0
    model_max_retries: int = 2
    run_timeout_s: float = 1200.0
    max_model_calls: int = 600
    """Chat model calls per run, the studio's and every sub-agent's together. A real run makes
    roughly 60 to 200 (see MIN_MODEL_CALLS); 600 leaves room for a long story with photos, a few
    compile and checker rounds and a second telling, while a looping sub-agent (which no step
    limit stops: see the module docstring) is stopped after at most 600 calls instead of running
    until STITCH_RUN_TIMEOUT_S."""
    art_layers: bool = True
    """STITCH_ART_LAYERS: False ("off") draws every set layer as a placeholder color layer without
    calling the image model; props, parts and states are still generated."""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RunLimits:
        """Limits from ``environ`` (default ``os.environ``); a bad value warns and keeps the default."""
        env = os.environ if environ is None else environ
        defaults = {f.name: f.default for f in fields(cls)}

        def read(name: str, field: str, parse: Callable[[str], Any], ok: Callable[[Any], bool], rule: str) -> Any:
            default = defaults[field]
            raw = env.get(name)
            if raw is None:
                return default
            value = parse(raw.strip()) if raw.strip() else None
            if value is None or not ok(value):
                _warn_once(name, raw, rule, default)
                return default
            return value

        return cls(
            max_images_per_run=read(
                ENV_MAX_IMAGES_PER_RUN, "max_images_per_run", _parse_int, lambda v: v >= 0, "a whole number >= 0"
            ),
            max_graph_steps=read(
                ENV_MAX_GRAPH_STEPS, "max_graph_steps", _parse_int, lambda v: v >= MIN_GRAPH_STEPS,
                f"a whole number >= {MIN_GRAPH_STEPS}",
            ),
            model_timeout_s=read(
                ENV_MODEL_TIMEOUT_S, "model_timeout_s", _parse_float, lambda v: v > 0, "a number of seconds > 0"
            ),
            model_max_retries=read(
                ENV_MODEL_MAX_RETRIES, "model_max_retries", _parse_int, lambda v: v >= 0, "a whole number >= 0"
            ),
            run_timeout_s=read(
                ENV_RUN_TIMEOUT_S, "run_timeout_s", _parse_float, lambda v: v > 0, "a number of seconds > 0"
            ),
            max_model_calls=read(
                ENV_MAX_MODEL_CALLS, "max_model_calls", _parse_int, lambda v: v >= MIN_MODEL_CALLS,
                f"a whole number >= {MIN_MODEL_CALLS}",
            ),
            art_layers=_read_switch(env, ENV_ART_LAYERS, defaults["art_layers"]),
        )

    def label(self) -> str:
        """A short summary for the production services line.

        The defaults read 'images=24 steps=1000 model_timeout=120s model_retries=2 run_timeout=1200s'.
        ``max_model_calls`` is not in it: the label is pinned by the production services line
        (tests/agent/test_agent_production.py); preflight's [limits] block shows it.
        """
        return (
            f"images={self.max_images_per_run} steps={self.max_graph_steps} "
            f"model_timeout={self.model_timeout_s:g}s model_retries={self.model_max_retries} "
            f"run_timeout={self.run_timeout_s:g}s"
        )


# --------------------------------------------------------------------------- usage per run


USAGE_RUNS_KEPT = 1024
"""How many runs ``RunUsage`` remembers; the least recently used is forgotten first."""


class RunBudgetExceeded(RuntimeError):
    """A run reached its model call cap (``RunLimits.max_model_calls``); the call was not made."""

    def __init__(self, run_id: str, limit: int) -> None:
        super().__init__(f"run {run_id} reached its limit of {limit} model calls")
        self.run_id = run_id
        self.limit = limit


@dataclass
class _Usage:
    model_calls: int = 0
    images_used: int = 0
    image_refusal_logged: bool = False


class RunUsage:
    """Model calls and images used, per run id. Thread-safe (tools and sub-agents run in
    parallel threads); keeps the last USAGE_RUNS_KEPT runs.

    One registry is shared by a run's ``ArtService`` (images), its ``ModelCallBudget`` (model
    calls) and its ``RunManager`` (which writes both into the run record and seeds them back on
    a restore), see ``agent.wiring.Services.usage``."""

    def __init__(self, *, keep: int = USAGE_RUNS_KEPT) -> None:
        self._runs: OrderedDict[str, _Usage] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def _entry(self, run_id: str) -> _Usage:
        """This run's usage (call with the lock held)."""
        entry = self._runs.get(run_id)
        if entry is None:
            entry = self._runs[run_id] = _Usage()
            while len(self._runs) > self._keep:
                self._runs.popitem(last=False)
        else:
            self._runs.move_to_end(run_id)
        return entry

    def model_calls(self, run_id: str) -> int:
        with self._lock:
            entry = self._runs.get(run_id)
            return entry.model_calls if entry is not None else 0

    def images_used(self, run_id: str) -> int:
        with self._lock:
            entry = self._runs.get(run_id)
            return entry.images_used if entry is not None else 0

    def counts(self, run_id: str) -> tuple[int, int]:
        """(model calls, images used) of ``run_id``."""
        with self._lock:
            entry = self._runs.get(run_id)
            return (entry.model_calls, entry.images_used) if entry is not None else (0, 0)

    def take_model_call(self, run_id: str, limit: int | None) -> int:
        """Count one model call for ``run_id`` and return the new count. Raises
        ``RunBudgetExceeded`` (and counts nothing) once the run has made ``limit`` calls;
        ``limit`` None counts without a cap."""
        with self._lock:
            entry = self._entry(run_id)
            if limit is not None and entry.model_calls >= limit:
                raise RunBudgetExceeded(run_id, limit)
            entry.model_calls += 1
            return entry.model_calls

    def take_image(self, run_id: str, limit: int) -> tuple[bool, bool]:
        """Count one image generation for ``run_id`` if it is under ``limit``.

        (taken, first refusal): taken is False (nothing counted) once the run has used
        ``limit`` images; first refusal is True the first time that happens in the run."""
        with self._lock:
            entry = self._entry(run_id)
            if entry.images_used < limit:
                entry.images_used += 1
                return True, False
            first = not entry.image_refusal_logged
            entry.image_refusal_logged = True
            return False, first

    def seed(self, run_id: str, *, model_calls: int = 0, images_used: int = 0) -> None:
        """Start ``run_id`` from counts persisted before a restart. A count never goes down:
        each becomes the larger of what is held and what is given."""
        with self._lock:
            entry = self._entry(run_id)
            entry.model_calls = max(entry.model_calls, max(0, int(model_calls)))
            entry.images_used = max(entry.images_used, max(0, int(images_used)))

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


class ModelCallBudget(BaseCallbackHandler):
    """Counts every chat model call of one run and refuses the one past its cap.

    A LangChain callback handler bound on the studio graph (``studio.build_studio``). Callbacks
    in a run's config reach every nested run: the studio's model calls, every sub-agent's
    (deepagents runs a sub-agent inside the ``task`` tool with the parent's callbacks), the
    general-purpose sub-agent deepagents adds and the summarization middleware's calls, which a
    middleware on the studio and the declared sub-agent specs would not all see.
    ``on_chat_model_start`` runs before the request is sent; ``raise_error`` makes the
    ``RunBudgetExceeded`` it raises propagate out of the model call, so the call is never made.
    """

    raise_error = True
    run_inline = True

    def __init__(self, run_id: str, usage: RunUsage, limit: int | None) -> None:
        super().__init__()
        self.run_id = run_id
        self.usage = usage
        self.limit = limit

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],  # noqa: ARG002
        messages: list[list[Any]],  # noqa: ARG002
        *,
        run_id: UUID,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self.usage.take_model_call(self.run_id, self.limit)

    def on_llm_start(
        self,
        serialized: dict[str, Any],  # noqa: ARG002
        prompts: list[str],  # noqa: ARG002
        *,
        run_id: UUID,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        # A plain (non-chat) LLM call: counted the same way.
        self.usage.take_model_call(self.run_id, self.limit)


__all__ = [
    "ENV_MAX_GRAPH_STEPS",
    "ENV_MAX_MODEL_CALLS",
    "ENV_MAX_IMAGES_PER_RUN",
    "ENV_MODEL_MAX_RETRIES",
    "ENV_MODEL_TIMEOUT_S",
    "ENV_RUN_TIMEOUT_S",
    "MIN_GRAPH_STEPS",
    "MIN_MODEL_CALLS",
    "ModelCallBudget",
    "RunBudgetExceeded",
    "RunLimits",
    "RunUsage",
    "env_value",
]
