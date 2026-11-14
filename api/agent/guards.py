"""Time and cost guards for one studio run: ``RunLimits``.

A run is bounded in four ways, each read from the environment by ``RunLimits.from_env``:

    STITCH_MAX_IMAGES_PER_RUN   image generations one run may make (default 24). 0 means none:
                                every prop and set layer gets placeholder art (null art + color).
                                Enforced by ``ArtService`` (agent/tools/art.py), per run id.
    STITCH_MAX_GRAPH_STEPS      graph steps per run, the LangGraph recursion limit (default 1000,
                                at least 50). Used by ``studio.thread_config``.
    STITCH_MODEL_TIMEOUT_S      seconds one Claude request may take (default 120). Used by
                                ``studio.make_model`` (ChatAnthropic ``timeout``).
    STITCH_MODEL_MAX_RETRIES    retries of a failed Claude request (default 2). Used by
                                ``studio.make_model`` (ChatAnthropic ``max_retries``).
    STITCH_RUN_TIMEOUT_S        seconds one run may take in all (default 1200). Parsed here;
                                enforced by the run manager (agent/runs.py).

A blank, unparsable or out-of-range value never stops the API: it logs one warning naming the
variable and the default is used. The warning is logged once per (variable, value) in a
process, since the limits are read again on every run.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

log = logging.getLogger(__name__)

ENV_MAX_IMAGES_PER_RUN = "STITCH_MAX_IMAGES_PER_RUN"
ENV_MAX_GRAPH_STEPS = "STITCH_MAX_GRAPH_STEPS"
ENV_MODEL_TIMEOUT_S = "STITCH_MODEL_TIMEOUT_S"
ENV_MODEL_MAX_RETRIES = "STITCH_MODEL_MAX_RETRIES"
ENV_RUN_TIMEOUT_S = "STITCH_RUN_TIMEOUT_S"

MIN_GRAPH_STEPS = 50
"""Below this a normal run cannot finish (the studio and its sub-agents take dozens of steps)."""

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


@dataclass(frozen=True)
class RunLimits:
    """The time and cost limits of one run (see the module docstring for the env vars)."""

    max_images_per_run: int = 24
    max_graph_steps: int = 1000
    model_timeout_s: float = 120.0
    model_max_retries: int = 2
    run_timeout_s: float = 1200.0

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
        )

    def label(self) -> str:
        """A short summary for the production services line, e.g. 'images=24 steps=1000 model_timeout=120s'."""
        return (
            f"images={self.max_images_per_run} steps={self.max_graph_steps} "
            f"model_timeout={self.model_timeout_s:g}s model_retries={self.model_max_retries}"
        )


__all__ = [
    "ENV_MAX_GRAPH_STEPS",
    "ENV_MAX_IMAGES_PER_RUN",
    "ENV_MODEL_MAX_RETRIES",
    "ENV_MODEL_TIMEOUT_S",
    "ENV_RUN_TIMEOUT_S",
    "MIN_GRAPH_STEPS",
    "RunLimits",
]
