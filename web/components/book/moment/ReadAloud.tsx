"use client";

/**
 * Read-aloud in the moment book, in the browser's own voice (../readAloud.ts):
 *
 *   <ReadAloudButton spread={spread} controller={player} />
 *       a paper "Read aloud" button for the open spread's story page: its
 *       title and words, aloud; "Stop" while it speaks. Mount it per spread
 *       (a `key`), so turning the page hushes it. Not shown where the
 *       browser cannot speak.
 *   <SpokenCaptions controller={player} />
 *       a silent telling (no narration recording for its teller) has its
 *       caption lines spoken as they are printed.
 *
 * Both are quiet while the book is muted (sound.ts, read only), and stop
 * when they unmount (the page turned, or left).
 */

import { useEffect, useSyncExternalStore } from "react";
import type { PlaybackController } from "@/components/stage/playback";
import { CaptionVoice, readAloud, type ReadAloud } from "../readAloud";
import { isMuted, subscribeMuted } from "../sound";
import type { BookSpread } from "../types";

const MUTE = { get: isMuted, subscribe: subscribeMuted };
const noSubscribe = () => () => {};
const NO_STATUS = () => null;

/** The page's speech engine, or null (no speech in this browser, or on the server). */
function useVoice(): ReadAloud | null {
  return useSyncExternalStore(noSubscribe, () => readAloud(MUTE), () => null);
}

/** What the story page says: the spread's own words, else the playing teller's. */
export function storyWords(spread: BookSpread, narrator: string | null): string {
  const own = spread.text.trim();
  const told = own || (spread.tellings ?? []).find((t) => t.id === narrator)?.text?.trim() || "";
  const title = spread.title.trim();
  return [title && !/[.!?…]$/.test(title) ? `${title}.` : title, told].filter(Boolean).join(" ");
}

export function ReadAloudButton({ spread, controller }: { spread: BookSpread; controller: PlaybackController | null }) {
  const voice = useVoice();
  const reading = useSyncExternalStore(
    voice?.subscribe ?? noSubscribe,
    () => voice?.reading ?? null,
    NO_STATUS,
  );
  const muted = useSyncExternalStore(subscribeMuted, isMuted, () => false);
  const narrator = useSyncExternalStore(
    controller?.subscribe ?? noSubscribe,
    () => controller?.getStatus().narrator ?? null,
    NO_STATUS,
  );
  // This spread's read ends with it (a turn, or leaving the book).
  useEffect(() => () => voice?.stop("story"), [voice]);

  const words = storyWords(spread, narrator);
  if (!voice || !words) return null;
  const speaking = reading === "story";
  const label = muted ? "Read aloud (sound is off)" : speaking ? "Stop reading aloud" : `Read aloud: ${spread.title}`;
  return (
    <button
      type="button"
      onClick={() => {
        if (speaking) voice.stop("story");
        else voice.read(words);
      }}
      aria-label={label}
      aria-disabled={muted || undefined}
      title={muted ? "Turn the sound on to hear the page" : speaking ? "Stop reading" : "Read this page aloud"}
      data-read-aloud={speaking ? "speaking" : "idle"}
      className={`pointer-events-auto fixed bottom-[calc(4.75rem+env(safe-area-inset-bottom))] right-4 z-10 flex h-11 cursor-pointer items-center gap-2 rounded-full px-4 font-display text-[0.95rem] outline-offset-2 outline-[#f3e6cf] transition-[background-color,box-shadow,scale,opacity] duration-200 hover:scale-105 focus-visible:outline-2 active:scale-95 xl:bottom-[calc(max(1rem,env(safe-area-inset-bottom))+0.25rem)] xl:right-6 ${
        speaking
          ? "bg-[#e3cfab] text-[#4a3c31] shadow-[inset_0_1px_3px_rgb(0_0_0/0.4),0_1px_0_rgb(255_255_255/0.12)]"
          : "bg-[#f5eee1]/95 text-[#5c4e43] shadow-[0_2px_3px_rgb(0_0_0/0.25),0_8px_20px_-6px_rgb(0_0_0/0.5)]"
      } ${muted ? "opacity-60" : ""}`}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {speaking ? (
          <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" fillOpacity="0.85" />
        ) : (
          <>
            {/* An open page, and the voice rising from it. */}
            <path d="M3 8.5c2.2-.9 4.4-.9 6.5.3v10c-2.1-1.2-4.3-1.2-6.5-.3z" fill="currentColor" fillOpacity="0.12" />
            <path d="M14 9.5a3.6 3.6 0 0 1 0 5" />
            <path d="M16.8 7a7.2 7.2 0 0 1 0 10" />
            <path d="M19.6 4.6a10.6 10.6 0 0 1 0 14.8" />
          </>
        )}
      </svg>
      <span aria-hidden>{speaking ? "Stop" : "Read aloud"}</span>
    </button>
  );
}

/** Speaks the captions of the open spread's telling while it is silent. Renders nothing. */
export function SpokenCaptions({ controller }: { controller: PlaybackController }) {
  const voice = useVoice();
  useEffect(() => {
    if (!voice) return;
    const spoken = new CaptionVoice(voice);
    const update = () => spoken.update(controller.getStatus(), controller.narrator, controller.duration);
    update();
    const off = controller.subscribe(update);
    return () => {
      off();
      spoken.dispose();
    };
  }, [voice, controller]);
  return null;
}
