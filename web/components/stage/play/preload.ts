/**
 * Getting a scene ready before it mounts, so the first frame is complete:
 * paper textures, the puppet kit (and each cast member's signature item), and
 * every set layer's placeholder cutout, each built in idle time.
 */

import { preloadPaperTextures } from "@/components/paper/materials";
import { preloadCutouts } from "../cutout";
import { preloadPuppetKit } from "../Puppet";
import { pickItem } from "../puppet/items";
import { sceneCutoutSpecs } from "./LayerBinding";
import type { Teller } from "./CaptionStrip";
import { personName, type SceneData } from "./scene";

export async function preloadScene(scene: SceneData): Promise<void> {
  const items = scene.people.map((p) => pickItem(p.character.item).kind);
  await preloadPaperTextures({ kinds: ["paper", "card"], wood: true });
  await Promise.all([preloadPuppetKit(items), preloadCutouts(sceneCutoutSpecs(scene.sets))]);
}

/** The tellers of a scene's timeline, default narrator first, with names and thread colours. */
export function sceneTellers(scene: SceneData): Teller[] {
  const tl = scene.timeline;
  const ids = Object.keys(tl.narrations ?? {});
  const first = tl.defaultNarrator && ids.includes(tl.defaultNarrator) ? tl.defaultNarrator : ids[0];
  const ordered = first ? [first, ...ids.filter((id) => id !== first)] : ids;
  return ordered.map((id) => ({
    id,
    name: personName(scene, id),
    color: scene.people.find((p) => p.id === id)?.character.color ?? "#b9573d",
  }));
}
