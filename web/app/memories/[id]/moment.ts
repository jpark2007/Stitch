/**
 * A moment for the book: its scene (compiled timeline and the sets, props
 * and people it names) and the one spread it is printed on.
 *
 * Sources, first match wins:
 *   1. A local sample (web/public/samples/<id>.{screenplay,timeline,memory}.json).
 *   2. With a family id (?family=<fid>): the saved moment from the API.
 * A saved moment with no compiled timeline still gets its spread, with no
 * stage on it.
 */

import { api, bareMediaId } from "@/lib/api";
import type { MemoryResponse, Screenplay, Timeline } from "@/lib/types";
import { SAMPLE_SCENES, sceneFromMemory, sceneTellers, withPageText, type SceneData } from "@/components/stage/play";
import type { BookCover, BookPhoto, BookPullTab, BookSpread, BookTeller } from "@/components/book/types";

export interface Moment {
  scene: SceneData | null;
  /** The moment's spread. */
  spread: BookSpread;
  /** The book's closing spread, to turn to (and back from). */
  end: BookSpread;
  cover: BookCover;
}

export const isSampleMoment = (id: string) => Object.hasOwn(SAMPLE_SCENES, id);

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Loads a moment by route id: a sample, or a saved one when a family id is given. */
export async function loadMoment(id: string, familyId: string | null, signal?: AbortSignal): Promise<Moment> {
  if (isSampleMoment(id)) {
    const base = SAMPLE_SCENES[id];
    const [screenplay, timeline, memory] = await Promise.all([
      getJson<Screenplay>(`${base}.screenplay.json`, signal),
      getJson<Timeline>(`${base}.timeline.json`, signal),
      getJson<MemoryResponse>(`${base}.memory.json`, signal),
    ]);
    const scene = sceneFromMemory(memory, withPageText(timeline, screenplay));
    // A sample's media are not served: it plays as silent timing.
    return momentFrom(memory, scene ? { ...scene, familyId: null } : null, null);
  }
  if (!familyId) throw new Error("no family");
  const res = await api.getMemory(familyId, id);
  return momentFrom(res, sceneFromMemory(res), familyId);
}

/** The spread and cover a moment is printed on. */
export function momentFrom(res: MemoryResponse, scene: SceneData | null, familyId: string | null): Moment {
  const memory = res.memory;
  const people = res.people ?? [];
  const tellings = memory.tellings ?? [];
  const nameOf = (pid: string) => people.find((p) => p.id === pid)?.name ?? pid;

  // One label per telling the scene can play (default teller first), with
  // their own words for a page that has no text of its own.
  const words = new Map(tellings.map((t) => [t.authorId, t.text]));
  const tellers: BookTeller[] = scene
    ? sceneTellers(scene).map((t) => ({ ...t, text: words.get(t.id) }))
    : tellings.map((t) => ({
        id: t.authorId,
        name: nameOf(t.authorId),
        color: people.find((p) => p.id === t.authorId)?.character.color ?? "#b9573d",
        text: t.text,
      }));

  // Storybook text: the shots' page text, read in order, as one short page.
  const shotText = (scene?.timeline.shots ?? [])
    .map((s) => s.pageText?.trim())
    .filter(Boolean)
    .join(" ");
  const playText = memory.screenplay?.shots?.map((s) => s.pageText?.trim()).filter(Boolean).join(" ") ?? "";
  const text = shotText || playText;

  const photoIds = [...new Set(tellings.flatMap((t) => t.photoIds ?? []))];
  const photos: BookPhoto[] = familyId
    ? photoIds.slice(0, 2).map((pid, i) => ({ id: pid, src: api.mediaUrl(familyId, bareMediaId(pid)), aspect: i ? 1 : 4 / 3 }))
    : [];

  // Pull tabs and sliders printed on the stage page (both pull out of a slit).
  const pullTabs: BookPullTab[] = (scene?.timeline.interactions ?? [])
    .filter((i) => (i.page ?? "stage") === "stage" && i.at.length >= 2)
    .map((i) => ({ id: i.id, label: i.label, at: [i.at[0], i.at[1]] as const }));

  const names = tellers.map((t) => t.name);
  const spread: BookSpread = {
    id: memory.id,
    title: memory.title,
    year: memory.year,
    text,
    tellers: names,
    tellings: tellers,
    photos,
    stage: scene ? { pullTabs } : undefined,
  };
  const told = names.length > 1 ? `${names.slice(0, -1).join(", ")} & ${names[names.length - 1]}` : names[0];
  const cover: BookCover = {
    kicker: "A family moment",
    title: memory.title,
    subtitle: told ? `${memory.year} · as told by ${told}` : String(memory.year),
  };
  const end: BookSpread = {
    id: `${memory.id}:end`,
    kind: "end",
    title: "The end",
    year: memory.year,
    text: told ? `as ${told} remember it` : "",
    tellings: tellers,
    photos: [],
  };
  return { scene, spread, end, cover };
}
