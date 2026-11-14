"""Generate web/lib/types.ts from the Pydantic contract models.

Run from the repo root:   cd api && uv run python ../shared/gen_types.py
Check only (used by tests): cd api && uv run python ../shared/gen_types.py --check

Never edit web/lib/types.ts by hand. Change api/schemas.py or api/screenplay/schema.py,
then regenerate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "api"))

from pydantic import BaseModel  # noqa: E402
from pydantic.json_schema import models_json_schema  # noqa: E402

import schemas  # noqa: E402
from screenplay import schema as screenplay_schema  # noqa: E402

OUT = REPO / "web" / "lib" / "types.ts"

HEADER = """// GENERATED FILE. Do not edit by hand.
// Source: api/schemas.py and api/screenplay/schema.py
// Regenerate: cd api && uv run python ../shared/gen_types.py
// Conventions (axes, units, channels): shared/CONTRACTS.md
"""


def _models() -> list[type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for module in (screenplay_schema, schemas):
        for name, obj in vars(module).items():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel and name != "Model":
                found[name] = obj
    return [found[name] for name in sorted(found)]


def _ts(node: dict) -> str:
    if "$ref" in node:
        return node["$ref"].split("/")[-1]
    if "enum" in node:
        return " | ".join(_lit(v) for v in node["enum"])
    if "const" in node:
        return _lit(node["const"])
    for key in ("anyOf", "oneOf"):
        if key in node:
            parts: list[str] = []
            for sub in node[key]:
                t = _ts(sub)
                if t not in parts:
                    parts.append(t)
            return " | ".join(parts)
    kind = node.get("type")
    if kind == "array":
        if "prefixItems" in node:
            return "[" + ", ".join(_ts(i) for i in node["prefixItems"]) + "]"
        inner = _ts(node.get("items", {}))
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if kind == "object":
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {_ts(extra)}>"
        return "Record<string, unknown>"
    if kind in ("number", "integer"):
        return "number"
    if kind == "string":
        return "string"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    return "unknown"


def _lit(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def generate() -> str:
    _, top = models_json_schema([(m, "validation") for m in _models()], ref_template="#/$defs/{model}")
    defs = top.get("$defs", {})
    out = [HEADER]
    for name in sorted(defs):
        node = defs[name]
        doc = node.get("description")
        if doc:
            out.append("/** " + " ".join(doc.split()) + " */")
        if node.get("type") != "object" or "properties" not in node:
            out.append(f"export type {name} = {_ts(node)};\n")
            continue
        required = set(node.get("required", []))
        out.append(f"export interface {name} {{")
        for prop, sub in node["properties"].items():
            desc = sub.get("description")
            if desc:
                out.append("  /** " + " ".join(desc.split()) + " */")
            opt = "" if prop in required else "?"
            out.append(f"  {prop}{opt}: {_ts(sub)};")
        out.append("}\n")

    vocab = screenplay_schema.VOCABULARY
    out.append("// From shared/vocabulary.json")
    for ts_name, values in (
        ("ActionVerb", list(vocab["actions"])),
        ("CameraMove", list(vocab["camera"])),
        ("EffectKind", vocab["effects"]),
        ("Transition", vocab["transitions"]),
        ("PropSize", list(vocab["sizes"])),
        ("Build", list(vocab["builds"])),
        ("InteractionKind", vocab["interactionKinds"]),
    ):
        out.append(f"export type {ts_name} = " + " | ".join(_lit(v) for v in values) + ";")
    out.append("")
    out.append("// From api/schemas.py")
    for alias in ("TraditionCategory", "TraditionWhenKind"):
        values = get_args(getattr(schemas, alias))
        out.append(f"export type {alias} = " + " | ".join(_lit(v) for v in values) + ";")
    out.append("")
    out.append("/** A mark is [u, h] or [u, h, d]. See shared/CONTRACTS.md. */")
    out.append("export type Mark = number[];")
    out.append("/** One timeline key: [time in seconds, value]. */")
    out.append("export type Key = [number, number | number[] | string | null];")
    out.append("")
    out.append("/** SSE event name -> payload type for GET /families/{fid}/runs/{id}/stream */")
    out.append("export interface RunEvents {")
    for event, model in schemas.RUN_EVENTS.items():
        out.append(f"  {event}: {model.__name__};")
    out.append("}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    text = generate()
    if "--check" in sys.argv:
        if OUT.read_text() != text:
            print("web/lib/types.ts is out of date. Run: cd api && uv run python ../shared/gen_types.py")
            sys.exit(1)
        print("web/lib/types.ts is up to date")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(REPO)}")
