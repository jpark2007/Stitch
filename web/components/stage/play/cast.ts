/**
 * The cast of a timeline stage: which entities a compiled timeline binds,
 * given the scene data its targets name. Pure (no React, no three), so it is
 * tested in node (cast.test.ts) and shared by every binding.
 *
 *   puppets  one per `puppet:<personId>` target, in frame order, with the
 *            person's character sheet (a warm stand-in sheet when the scene
 *            data lacks the person)
 *   props    one per `prop:<propId>` target, with the Prop (a hand-sized
 *            placeholder card when the scene data lacks it)
 *   sets     every set the layer tracks fold, with the layer indices that
 *            exist (back first) and their frame slots
 *   issues   what the stage could not place as authored: missing people,
 *            props, sets or layers, and shots on a set nobody describes.
 *            The provider logs them once with console.warn, so bad scene
 *            data is visible while authoring; the scene still plays.
 */

import type { Person, Prop, SetDef } from "@/lib/types";
import type { PlaybackFrame, SetLayerFrame } from "../playback";
import type { SceneData } from "./scene";

export interface CastPuppet {
  /** Person id (`puppet:<id>`) */
  id: string;
  /** Order in the frame, for the actors' small depth stagger */
  index: number;
  person: Person;
  /** The person came from the scene data (false: a stand-in sheet) */
  known: boolean;
}

export interface CastProp {
  prop: Prop;
  /** Drawn as a placeholder card: the scene data has no such prop */
  placeholder: boolean;
}

export interface CastSet {
  set: SetDef;
  /** Layer indices with a fold track, back layers first */
  layers: number[];
  /** Their fold slots in the frame (same order as the tracks, not `layers`) */
  folds: SetLayerFrame[];
}

export interface SceneCast {
  puppets: readonly CastPuppet[];
  props: readonly CastProp[];
  sets: readonly CastSet[];
  issues: readonly string[];
}

type CastFrame = Pick<PlaybackFrame, "puppets" | "props" | "layers">;
type CastScene = Pick<SceneData, "familyId" | "timeline" | "sets" | "props" | "people">;

/** Warm stand-in colours for cast the scene data does not describe. */
const FALLBACK_COLORS = ["#8b5a7c", "#5b82a6", "#7f9873", "#d3a03d", "#b9573d"];

/**
 * A person by id: from the scene data, else a stand-in sheet in a warm
 * colour (the same one every time for the same id).
 */
export function personLookup(scene: Pick<SceneData, "familyId" | "people">): (id: string) => Person {
  const people = new Map(scene.people.map((p) => [p.id, p]));
  const fallback = new Map<string, Person>();
  return (id) => {
    const hit = people.get(id) ?? fallback.get(id);
    if (hit) return hit;
    const made: Person = {
      id,
      familyId: scene.familyId ?? "",
      name: id,
      character: { color: FALLBACK_COLORS[fallback.size % FALLBACK_COLORS.length], build: "average" },
    };
    fallback.set(id, made);
    return made;
  };
}

/** A tracked prop the scene data does not describe: a hand-sized card. */
export function placeholderProp(id: string): Prop {
  return { id, name: id, size: "hand", art: { body: null }, color: "#c4a37a", aspect: 1, anchor: [0.5, 1], grip: [0.5, 0.5] };
}

/**
 * The sets the layer tracks fold, each with the layers that exist (back
 * first: they rise first and fold on top). Tracks naming a missing set or
 * layer are reported in `issues` (when given) and skipped.
 */
export function setLayersOf(sets: readonly SetDef[], layers: readonly SetLayerFrame[], issues?: string[]): CastSet[] {
  const byId = new Map(sets.map((s) => [s.id, s]));
  const out: CastSet[] = [];
  for (const lf of layers) {
    const set = byId.get(lf.setId);
    if (!set) {
      issues?.push(`${lf.target}: no set "${lf.setId}" in the scene data; not drawn`);
      continue;
    }
    if (!set.layers[lf.layer]) {
      issues?.push(`${lf.target}: set "${set.id}" has ${set.layers.length} layer(s); not drawn`);
      continue;
    }
    let entry = out.find((e) => e.set.id === set.id);
    if (!entry) {
      entry = { set, layers: [], folds: [] };
      out.push(entry);
    }
    entry.layers.push(lf.layer);
    entry.folds.push(lf);
  }
  for (const e of out) e.layers.sort((a, b) => e.set.layers[a].depth - e.set.layers[b].depth || a - b);
  return out;
}

/** Everything `frame` (a compiled timeline's frame) binds against `scene`. */
export function castScene(scene: CastScene, frame: CastFrame, person = personLookup(scene)): SceneCast {
  const issues: string[] = [];
  const known = new Set(scene.people.map((p) => p.id));
  const puppets = frame.puppets.map((pf, index) => {
    const ok = known.has(pf.id);
    if (!ok) issues.push(`${pf.target}: no person "${pf.id}" in the scene data; drawn with a stand-in sheet`);
    return { id: pf.id, index, person: person(pf.id), known: ok };
  });
  const propsById = new Map(scene.props.map((p) => [p.id, p]));
  const props = frame.props.map((pf) => {
    const prop = propsById.get(pf.id);
    if (!prop) issues.push(`${pf.target}: no prop "${pf.id}" in the scene data; drawn as a placeholder card`);
    return prop ? { prop, placeholder: false } : { prop: placeholderProp(pf.id), placeholder: true };
  });
  const sets = setLayersOf(scene.sets, frame.layers, issues);
  const setIds = new Set(scene.sets.map((s) => s.id));
  for (const shot of scene.timeline.shots ?? []) {
    if (!setIds.has(shot.set)) issues.push(`shot ${shot.id}: no set "${shot.set}" in the scene data`);
  }
  return { puppets, props, sets, issues };
}
