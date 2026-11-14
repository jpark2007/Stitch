"use client";

/**
 * The timeline stage mounted in the 3D book's right-page slot, exactly as the
 * book will mount it: Book3D's renderStage hands each spread its slot size
 * and `reveal`, and <TimelineScene> plays the sample inside the book's own
 * Canvas, lights and camera. Proves the embedding API before the book wires
 * real moments.
 */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Book3D } from "@/components/book/Book3D";
import { demoCover, demoSpreads } from "@/components/book/demoSpreads";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import type { BookSpread } from "@/components/book/types";
import { useTimelinePlayer } from "@/components/stage/playback";
import { TimelineScene, loadSampleScene, preloadScene, sceneTellers, type SceneData } from "@/components/stage/play";

function SpreadStage({ scene, args, time }: { scene: SceneData; args: StageSlotArgs; time: number | null }) {
  // The book's Canvas draws on demand: the scene ticks the clock in its own
  // render loop and keeps it drawing only while it plays.
  const { controller } = useTimelinePlayer(scene.timeline, {
    autoplay: time === null,
    loop: true,
    freezeAt: time,
    audio: false,
    clock: "manual",
  });
  const size = useMemo<[number, number]>(() => [args.width, args.depth], [args.width, args.depth]);
  return <TimelineScene controller={controller} scene={scene} size={size} reveal={args.reveal} />;
}

export function BookStageLab() {
  const q = useSearchParams();
  const [scene, setScene] = useState<SceneData | null>(null);
  const t = q.get("t");
  const time = t !== null && Number.isFinite(Number(t)) ? Number(t) : null;

  useEffect(() => {
    const ctl = new AbortController();
    loadSampleScene("hymn_books", ctl.signal)
      .then(async (s) => {
        await preloadScene(s);
        if (!ctl.signal.aborted) setScene(s);
      })
      .catch(() => {});
    return () => ctl.abort();
  }, []);

  const spreads = useMemo<BookSpread[]>(() => {
    if (!scene) return demoSpreads;
    const first: BookSpread = {
      ...demoSpreads[0],
      id: scene.id,
      title: scene.title,
      year: scene.year ?? demoSpreads[0].year,
      text: scene.timeline.shots?.[0]?.pageText ?? demoSpreads[0].text,
      tellers: sceneTellers(scene).map((x) => x.name),
    };
    return [first, ...demoSpreads.slice(1)];
  }, [scene]);

  if (!scene) return null;
  return (
    <Book3D
      spreads={spreads}
      cover={demoCover}
      initial={{ state: "open", spread: 1, focus: q.get("focus") === "right" ? 1 : 0 }}
      still={q.get("still") === "1"}
      renderStage={(args) => (args.spread.id === scene.id ? <SpreadStage scene={scene} args={args} time={time} /> : null)}
    />
  );
}
