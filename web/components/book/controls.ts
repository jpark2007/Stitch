import type { Object3D } from "three";
import type { BookRuntime } from "./runtime";

/**
 * Controls a spread lays on its pages (pull tabs). The book's own invisible
 * page target lies over them: it leaves an event alone, for the control to
 * handle, when the pointer is over one, or while one is held (the pointer
 * runs ahead of the paper it pulls, so no corner peels or turns meanwhile).
 */
const KEY = "bookControl";

/** Mark `object` (and so everything under it) as a page control. */
export function markBookControl(object: Object3D | null) {
  if (object) object.userData[KEY] = true;
}

/** `object` or one of its ancestors is a page control. */
export function isBookControl(object: Object3D | null): boolean {
  for (let o = object; o; o = o.parent) if (o.userData[KEY]) return true;
  return false;
}

/**
 * A control on this book's page is held: the book leaves the pointer alone
 * until the returned release is called (once; later calls do nothing).
 */
export function holdBookControl(rt: BookRuntime | null): () => void {
  if (!rt) return () => {};
  rt.controlsHeld++;
  let done = false;
  return () => {
    if (done) return;
    done = true;
    rt.controlsHeld--;
  };
}
