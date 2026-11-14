/**
 * The telling a moment's spread shows as playing, read from its player, for
 * Book3D's `tellings`: the teller being heard, or none once the scene has
 * ended (every label then offers its telling again, the last one included).
 *
 * The book subscribes to it directly, so the player's status updates (each
 * caption, play, pause) never re-render React: listeners hear only a change
 * of teller, and the book reprints just that story page.
 */

import type { PlaybackController } from "@/components/stage/playback";
import type { TellingsSource } from "../Book3D";

export function playingTellings(controller: PlaybackController, spreadId: string): TellingsSource {
  const read = () => {
    const s = controller.getStatus();
    return s.ended ? null : s.narrator;
  };
  let id = read();
  let value: Readonly<Record<string, string>> = id ? { [spreadId]: id } : {};
  const get = () => {
    const now = read();
    if (now !== id) {
      id = now;
      value = now ? { [spreadId]: now } : {};
    }
    return value;
  };
  return {
    get,
    subscribe: (listener) => {
      let seen = get();
      return controller.subscribe(() => {
        const now = get();
        if (now === seen) return;
        seen = now;
        listener();
      });
    },
  };
}
