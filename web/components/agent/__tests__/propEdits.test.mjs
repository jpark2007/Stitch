// Run with: cd web && node --test components/agent/__tests__/
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  initialPropDecisions,
  validatePropDecisions,
  toPropEdits,
  propSizeLabel,
  PROP_SIZE_LABELS,
  REUSED_PROP_LABEL,
  PROP_OPTION_CHIP_CLASS,
  GENERIC_UPLOAD_ERROR,
  uploadErrorMessage,
  dropFailedPhoto,
} from "../propEdits.ts";

const chips = [
  { id: "hymn_books", name: "Hymn books", size: "hand" },
  { id: "pew", name: "Church pew", size: "furniture", reused: true },
  { id: "car", name: "Blue Buick", size: "vehicle" },
];

test("all chips default to keep", () => {
  const d = initialPropDecisions(chips);
  assert.deepEqual(d, {
    hymn_books: { action: "keep" },
    pew: { action: "keep" },
    car: { action: "keep" },
  });
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d), [
    { id: "hymn_books", action: "keep" },
    { id: "pew", action: "keep" },
    { id: "car", action: "keep" },
  ]);
});

test("empty chip list", () => {
  assert.deepEqual(initialPropDecisions([]), {});
  assert.deepEqual(toPropEdits([], {}), []);
});

test("rename trims the name", () => {
  const d = { ...initialPropDecisions(chips), pew: { action: "rename", name: "  Back pew  " } };
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d)[1], { id: "pew", action: "rename", name: "Back pew" });
});

test("rename rejects blank names", () => {
  for (const name of ["", "   "]) {
    const d = { ...initialPropDecisions(chips), pew: { action: "rename", name } };
    const errors = validatePropDecisions(chips, d);
    assert.deepEqual(Object.keys(errors), ["pew"]);
    assert.equal(typeof errors.pew, "string");
  }
});

test("rename allows 60 characters and rejects 61", () => {
  const ok = { ...initialPropDecisions(chips), car: { action: "rename", name: "x".repeat(60) } };
  assert.deepEqual(validatePropDecisions(chips, ok), {});
  const long = { ...initialPropDecisions(chips), car: { action: "rename", name: "x".repeat(61) } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, long)), ["car"]);
  // Length is measured after trimming.
  const padded = { ...initialPropDecisions(chips), car: { action: "rename", name: `  ${"x".repeat(60)}  ` } };
  assert.deepEqual(validatePropDecisions(chips, padded), {});
});

test("rename rejects an unchanged name", () => {
  const d = { ...initialPropDecisions(chips), hymn_books: { action: "rename", name: " Hymn books " } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, d)), ["hymn_books"]);
});

test("remove produces a remove edit", () => {
  const d = { ...initialPropDecisions(chips), car: { action: "remove" } };
  assert.deepEqual(validatePropDecisions(chips, d), {});
  assert.deepEqual(toPropEdits(chips, d)[2], { id: "car", action: "remove" });
});

test("replace_photo requires a photoId", () => {
  const missing = {
    ...initialPropDecisions(chips),
    hymn_books: { action: "replace_photo", photoId: null, fileName: "books.jpg" },
  };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, missing)), ["hymn_books"]);
  const empty = { ...initialPropDecisions(chips), hymn_books: { action: "replace_photo", photoId: "" } };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, empty)), ["hymn_books"]);

  const ok = {
    ...initialPropDecisions(chips),
    hymn_books: { action: "replace_photo", photoId: "media_123", fileName: "books.jpg" },
  };
  assert.deepEqual(validatePropDecisions(chips, ok), {});
  assert.deepEqual(toPropEdits(chips, ok)[0], {
    id: "hymn_books",
    action: "replace_photo",
    photoId: "media_123",
  });
});

test("output keeps chip order, not decision order", () => {
  const d = {
    car: { action: "remove" },
    pew: { action: "rename", name: "Pew" },
    hymn_books: { action: "keep" },
  };
  assert.deepEqual(
    toPropEdits(chips, d).map((e) => [e.id, e.action]),
    [
      ["hymn_books", "keep"],
      ["pew", "rename"],
      ["car", "remove"],
    ],
  );
});

test("missing decisions count as keep and unknown ids are ignored", () => {
  const d = { pew: { action: "remove" }, ghost: { action: "remove" } };
  assert.deepEqual(toPropEdits(chips, d), [
    { id: "hymn_books", action: "keep" },
    { id: "pew", action: "remove" },
    { id: "car", action: "keep" },
  ]);
});

test("toPropEdits throws when validation fails", () => {
  const d = {
    ...initialPropDecisions(chips),
    pew: { action: "rename", name: "" },
    car: { action: "replace_photo", photoId: null },
  };
  assert.deepEqual(Object.keys(validatePropDecisions(chips, d)).sort(), ["car", "pew"]);
  assert.throws(() => toPropEdits(chips, d), /pew/);
});

test("every prop size reads as a plain size word", () => {
  assert.equal(propSizeLabel("tiny"), "Tiny");
  assert.equal(propSizeLabel("hand"), "Hand-sized");
  assert.equal(propSizeLabel("carried"), "Armful");
  assert.equal(propSizeLabel("furniture"), "Furniture-sized");
  assert.equal(propSizeLabel("vehicle"), "Car-sized");
  assert.equal(propSizeLabel("building"), "Building-sized");
  // "Carried" describes an action, not a size.
  assert.notEqual(propSizeLabel("carried"), "Carried");
  // The table covers exactly the PropChip sizes, and each one ends in "-sized"
  // or is one of the plain size words.
  assert.deepEqual(Object.keys(PROP_SIZE_LABELS).sort(), [
    "building",
    "carried",
    "furniture",
    "hand",
    "tiny",
    "vehicle",
  ]);
  for (const label of Object.values(PROP_SIZE_LABELS)) {
    assert.ok(label.endsWith("-sized") || ["Tiny", "Armful"].includes(label), label);
  }
});

test("unknown sizes are humanized, never shown raw", () => {
  assert.equal(propSizeLabel("big_box"), "Big box");
  assert.equal(propSizeLabel("room-sized"), "Room sized");
  assert.equal(propSizeLabel("  WALL__HANGING "), "Wall hanging");
  assert.equal(propSizeLabel("handHeld"), "Hand held");
  assert.equal(propSizeLabel(""), "");
  // Object prototype keys are not sizes.
  assert.equal(propSizeLabel("toString"), "To string");
  assert.ok(!propSizeLabel("big_box").includes("_"));
});

test("the reused pill says the prop is already the family's, not 'saved'", () => {
  assert.equal(REUSED_PROP_LABEL, "used before");
  assert.notEqual(REUSED_PROP_LABEL.toLowerCase(), "saved");
});

// ---- Option chip colours: the chosen chip stays readable under the pointer ----

// Colour-only utilities that hover and peer-checked could fight over.
const SIZE_SUFFIX = /^(xs|sm|base|lg|xl|\d+xl|\[.*\])$/;
const WIDTH_SUFFIX = /^(\d+|x|y|t|r|b|l|s|e|x-\d+|y-\d+|[trblse]-\d+|solid|dashed|dotted|double|none|hidden)$/;
function colourProp(utility) {
  const m = /^(text|border|bg)-(.+)$/.exec(utility);
  if (!m) return null;
  const [, prop, value] = m;
  if (prop === "text" && SIZE_SUFFIX.test(value)) return null;
  if (prop === "border" && WIDTH_SUFFIX.test(value)) return null;
  return { prop, value };
}
// Split "a:b-[:x]:c" on the colons outside square brackets.
function splitVariants(token) {
  const parts = [];
  let depth = 0;
  let cur = "";
  for (const ch of token) {
    if (ch === "[") depth++;
    else if (ch === "]") depth--;
    if (ch === ":" && depth === 0) {
      parts.push(cur);
      cur = "";
    } else cur += ch;
  }
  parts.push(cur);
  return parts;
}
const UNCHECKED_QUALIFIERS = new Set(["peer-not-checked", "peer-[:not(:checked)]"]);

/**
 * Hover colour utilities that would win over the chosen (peer-checked) colour
 * of the same property: a `hover:` colour that is neither limited to the
 * unchecked state nor matched by `peer-checked:hover:<prop>-<checked value>`.
 */
function hoverCheckedConflicts(className) {
  const tokens = className.split(/\s+/).filter(Boolean);
  const parsed = tokens.map((t) => {
    const parts = splitVariants(t);
    return { token: t, variants: parts.slice(0, -1), colour: colourProp(parts[parts.length - 1]) };
  });
  const checked = parsed.filter(
    (p) => p.colour && p.variants.length === 1 && p.variants[0] === "peer-checked",
  );
  const conflicts = [];
  for (const p of parsed) {
    if (!p.colour || !p.variants.includes("hover")) continue;
    if (p.variants.includes("peer-checked")) continue; // this is the chosen-state override
    if (p.variants.some((v) => UNCHECKED_QUALIFIERS.has(v))) continue;
    for (const c of checked.filter((c) => c.colour.prop === p.colour.prop)) {
      const override = `peer-checked:hover:${c.colour.prop}-${c.colour.value}`;
      if (!tokens.includes(override)) conflicts.push(`${p.token} vs ${c.token}`);
    }
  }
  return conflicts;
}

// The chip class before the fix: hover:text-ink beat peer-checked:text-paper-light.
const OLD_CHIP_CLASS =
  "inline-flex min-h-8 items-center rounded-card border border-ink-faint bg-paper-light px-3 text-sm leading-none text-ink-soft transition-colors duration-150 ease-paper hover:border-ink-soft hover:text-ink peer-checked:border-ink peer-checked:bg-ink peer-checked:text-paper-light peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent peer-disabled:cursor-not-allowed peer-disabled:opacity-60";

test("the conflict check catches the old chip class (ink text on ink when hovered)", () => {
  const conflicts = hoverCheckedConflicts(OLD_CHIP_CLASS);
  assert.ok(conflicts.includes("hover:text-ink vs peer-checked:text-paper-light"), conflicts.join("; "));
  assert.ok(conflicts.includes("hover:border-ink-soft vs peer-checked:border-ink"), conflicts.join("; "));
  // Either fix shape is accepted.
  assert.deepEqual(
    hoverCheckedConflicts(
      `${OLD_CHIP_CLASS} peer-checked:hover:text-paper-light peer-checked:hover:border-ink`,
    ),
    [],
  );
  assert.deepEqual(
    hoverCheckedConflicts("text-ink-soft peer-[:not(:checked)]:hover:text-ink peer-checked:text-paper-light"),
    [],
  );
});

test("a chosen option chip keeps paper-light text on ink, hovered or not", () => {
  assert.deepEqual(hoverCheckedConflicts(PROP_OPTION_CHIP_CLASS), []);
  const tokens = PROP_OPTION_CHIP_CLASS.split(/\s+/);
  for (const t of ["peer-checked:bg-ink", "peer-checked:border-ink", "peer-checked:text-paper-light"]) {
    assert.ok(tokens.includes(t), `missing ${t}`);
  }
  // Unchosen chips still darken on hover.
  assert.ok(tokens.some((t) => t.endsWith("hover:text-ink") && !t.startsWith("peer-checked")));
  assert.ok(tokens.some((t) => t.endsWith("hover:border-ink-soft") && !t.startsWith("peer-checked")));
  // Focus ring and disabled look are untouched.
  for (const t of [
    "peer-focus-visible:outline-2",
    "peer-focus-visible:outline-offset-2",
    "peer-focus-visible:outline-accent",
    "peer-disabled:cursor-not-allowed",
    "peer-disabled:opacity-60",
  ]) {
    assert.ok(tokens.includes(t), `missing ${t}`);
  }
});

test("uploadErrorMessage: the client's 4xx sentence, the generic line otherwise", () => {
  const err = (status, message) => Object.assign(new Error(message), { status });
  assert.equal(uploadErrorMessage(err(413, "That file is too large.")), "That file is too large.");
  assert.equal(uploadErrorMessage(err(415, "Upload a JPEG, PNG, WebP, GIF or HEIC image")), "Upload a JPEG, PNG, WebP, GIF or HEIC image");
  // The server's 5xx wording can carry internals: never shown.
  for (const status of [500, 502, 503]) assert.equal(uploadErrorMessage(err(status, "disk full")), GENERIC_UPLOAD_ERROR);
  assert.equal(uploadErrorMessage(err(0, "We can't reach Stitch right now.")), GENERIC_UPLOAD_ERROR);
  assert.equal(uploadErrorMessage(new TypeError("Failed to fetch")), GENERIC_UPLOAD_ERROR);
  assert.equal(uploadErrorMessage(null), GENERIC_UPLOAD_ERROR);
  assert.equal(uploadErrorMessage(err(422, "  ")), GENERIC_UPLOAD_ERROR);
});

test("dropFailedPhoto: a photo that did not upload loses its preview and id, keeps its name", () => {
  assert.deepEqual(dropFailedPhoto({ photoId: null, fileName: "pew.jpg", previewUrl: "blob:1" }), {
    draft: { photoId: null, fileName: "pew.jpg" },
    revoke: "blob:1",
  });
  // A photo id from an earlier pick never survives a failed new one.
  const out = dropFailedPhoto({ photoId: "media_1", fileName: "pew.jpg", previewUrl: "blob:2" });
  assert.equal(out.draft.photoId, null);
  assert.equal("previewUrl" in out.draft, false);
  assert.equal(out.revoke, "blob:2");
  assert.deepEqual(dropFailedPhoto(undefined), { draft: { photoId: null }, revoke: null });
  assert.deepEqual(dropFailedPhoto({ photoId: null }), { draft: { photoId: null }, revoke: null });
  // So the card asks for a photo again instead of sending one.
  const chip = [{ id: "pew", name: "Church pew", size: "furniture" }];
  const decision = { pew: { action: "replace_photo", ...out.draft } };
  assert.deepEqual(validatePropDecisions(chip, decision), { pew: "Choose a photo, or keep the drawing." });
});
