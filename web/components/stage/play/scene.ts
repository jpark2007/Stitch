/**
 * What the timeline stage plays: a compiled Timeline plus the scene data its
 * track targets name (CONTRACTS.md "Timeline"). Exactly what a MemoryResponse
 * carries: the memory's sets (layers and marks), props and people (character
 * sheets). The stage is a dumb player, so this is all it needs.
 */

import type { MemoryResponse, Person, Prop, SetDef, Timeline } from "@/lib/types";

export interface SceneData {
  /** The memory id ("sample_hymn_books") */
  id: string;
  title: string;
  year: number | null;
  /** Family id, to resolve `media/<id>` references; null for local samples */
  familyId: string | null;
  timeline: Timeline;
  sets: readonly SetDef[];
  props: readonly Prop[];
  people: readonly Person[];
}

/**
 * The scene of a MemoryResponse. `timeline` overrides the memory's own (for
 * a timeline fetched or compiled separately). Null when there is nothing to
 * play.
 */
export function sceneFromMemory(res: MemoryResponse, timeline?: Timeline | null): SceneData | null {
  const tl = timeline ?? res.memory.timeline ?? null;
  if (!tl) return null;
  return {
    id: res.memory.id,
    title: res.memory.title,
    year: res.memory.year ?? null,
    familyId: res.memory.familyId ?? null,
    timeline: tl,
    sets: res.sets ?? [],
    props: res.props ?? [],
    people: res.people ?? [],
  };
}

/** Local reference scenes under web/public/samples, by route id. */
export const SAMPLE_SCENES: Readonly<Record<string, string>> = {
  hymn_books: "/samples/hymn_books",
};

/**
 * Loads a sample's three JSON files (screenplay, timeline, memory) and
 * returns its scene. The screenplay is fetched to keep the trio together
 * (and fail loudly if one goes missing) though the stage only plays the
 * timeline.
 */
export async function loadSampleScene(name: string, signal?: AbortSignal): Promise<SceneData> {
  const base = SAMPLE_SCENES[name];
  if (!base) throw new Error(`unknown sample "${name}"`);
  const get = async <T,>(suffix: string): Promise<T> => {
    const res = await fetch(`${base}.${suffix}.json`, { signal });
    if (!res.ok) throw new Error(`${base}.${suffix}.json: HTTP ${res.status}`);
    return (await res.json()) as T;
  };
  const [, timeline, memory] = await Promise.all([
    get<unknown>("screenplay"),
    get<Timeline>("timeline"),
    get<MemoryResponse>("memory"),
  ]);
  const scene = sceneFromMemory(memory, timeline);
  if (!scene) throw new Error(`${name}: no timeline`);
  return { ...scene, familyId: null };
}

/** A person's display name by id, falling back to the id itself. */
export function personName(scene: Pick<SceneData, "people">, id: string): string {
  return scene.people.find((p) => p.id === id)?.name ?? id;
}
