/**
 * The shared music AudioContext's lifecycle as a pure step, and the hub that
 * carries its effects out over injected dependencies (createMusicHub), so the
 * tests drive the real thing with a fake context (hubState.test.ts). hub.ts
 * wires it to the window, the book's mute and a real AudioContext. Free of
 * runtime imports.
 *
 *   - Made only inside a reader's gesture (pointerdown, keydown, or the click
 *     that unmutes), only while an engine is waiting for it, never while muted
 *     (the book's rule, sound.ts).
 *   - Running while some engine is awake (its transport wants sound), else
 *     suspended: a quiet book costs the audio thread nothing.
 *   - A gesture also resumes the context for an awake engine: iOS will not
 *     resume a suspended (or "interrupted") context outside a gesture, so a
 *     context put to sleep while muted or closed wakes on the next tap.
 *   - Gesture listeners exist only while some engine is attached.
 *
 * Fades are not the hub's business: each engine fades its own level before
 * it lets go of the context (fades.ts stepTransport).
 */

export type HubStatus = "none" | "running" | "suspended";

export interface HubState {
  status: HubStatus;
  muted: boolean;
  /** Engines attached (waiting for the context, or using it). */
  engines: number;
  /** Some engine wants the context running. */
  awake: boolean;
}

export type HubEvent =
  | { type: "attach" }
  | { type: "detach" }
  | { type: "gesture" }
  | { type: "mute"; on: boolean }
  | { type: "awake"; on: boolean };

export type HubEffect =
  /** Construct the context (and hand it to the engines). */
  | "create"
  | "resume"
  | "suspend"
  /** Let the book's own paper sounds unlock on the same gesture. */
  | "unlockBook"
  /** Add / remove the window's gesture listeners. */
  | "listen"
  | "unlisten";

export const INITIAL_HUB: HubState = { status: "none", muted: false, engines: 0, awake: false };

export function hubStep(prev: HubState, ev: HubEvent): { state: HubState; effects: HubEffect[] } {
  const s: HubState = { ...prev };
  const fx: HubEffect[] = [];

  switch (ev.type) {
    case "attach":
      s.engines++;
      if (s.engines === 1) fx.push("listen");
      break;
    case "detach":
      s.engines = Math.max(0, s.engines - 1);
      if (s.engines === 0 && prev.engines > 0) fx.push("unlisten");
      break;
    case "mute":
      s.muted = ev.on;
      // The unmute click is a gesture.
      if (!ev.on) gesture(s, fx);
      break;
    case "gesture":
      if (!s.muted) {
        fx.push("unlockBook");
        gesture(s, fx);
      }
      break;
    case "awake":
      s.awake = ev.on;
      if (ev.on && s.status === "suspended") {
        fx.push("resume");
        s.status = "running";
      } else if (!ev.on && s.status === "running") {
        fx.push("suspend");
        s.status = "suspended";
      }
      break;
  }
  return { state: s, effects: fx };
}

function gesture(s: HubState, fx: HubEffect[]) {
  if (s.status === "none") {
    if (s.engines === 0) return;
    fx.push("create");
    // Nobody wants to play yet: sleep rather than run silence.
    if (s.awake) s.status = "running";
    else {
      fx.push("suspend");
      s.status = "suspended";
    }
    return;
  }
  // Ask even when it looks running: on iOS it may be "interrupted".
  if (s.awake) {
    fx.push("resume");
    s.status = "running";
  }
}

/**
 * hubStep driven by events, never re-entered. Carrying out an effect can
 * raise a new event: "create" hands the context to the engines, and one
 * that already has an era wakes ("awake") from inside that call. Stepping it
 * there, mid-list, would apply its "resume" and then the outer list's stale
 * "suspend" (new, resume, suspend: silent until the next tap). So an event
 * raised while effects are being applied waits until the current list is
 * done (new, suspend, resume), and every effect still runs inside the
 * gesture that caused it.
 */
export function createHubDispatcher(apply: (fx: HubEffect) => void, initial: HubState = INITIAL_HUB) {
  let state = initial;
  const queue: HubEvent[] = [];
  let busy = false;
  return {
    get state() {
      return state;
    },
    dispatch(ev: HubEvent) {
      queue.push(ev);
      if (busy) return;
      busy = true;
      try {
        for (let next = queue.shift(); next; next = queue.shift()) {
          const r = hubStep(state, next);
          state = r.state;
          for (const fx of r.effects) apply(fx);
        }
      } finally {
        // An effect that throws drops the events behind it rather than
        // leaving the hub deaf to every later one.
        queue.length = 0;
        busy = false;
      }
    },
  };
}

/** The little of an AudioContext the hub touches. */
export interface HubContext {
  readonly state: string;
  resume(): Promise<void>;
  suspend(): Promise<void>;
}

export interface HubDeps<C extends HubContext> {
  isMuted(): boolean;
  subscribeMuted(cb: (muted: boolean) => void): () => void;
  /** Unlock the book's own paper sounds on the same gesture. */
  unlockAudio(): void;
  /** Construct the context; null where there is no WebAudio. */
  makeContext(): C | null;
  /** Add or remove the window's pointerdown/keydown listeners. */
  listen(on: boolean, onGesture: () => void): void;
}

export interface MusicHub<C extends HubContext> {
  context(): C | null;
  /**
   * Call `cb` with the context as soon as it exists: now, if it does, else
   * inside the reader's next pointer or key press (or unmute click).
   * Returns the detach; the listeners go away with the last engine.
   */
  attach(cb: (c: C) => void): () => void;
  /** This engine wants the context running. */
  wake(owner: object): void;
  /** It does not; suspended once no engine does. */
  sleep(owner: object): void;
  /** Some engine holds the context awake. */
  awake(): boolean;
  /** The hub's own view (hubStep's state), for the lab and the tests. */
  state(): HubState;
}

export function createMusicHub<C extends HubContext>(deps: HubDeps<C>): MusicHub<C> {
  let ctx: C | null = null;
  const clients = new Set<(c: C) => void>();
  const awake = new Set<object>();
  let unsubscribeMute: (() => void) | null = null;

  // Not re-entrant: an engine woken inside "create" is stepped after the
  // gesture's own effects, so its "resume" comes last and wins.
  const hub = createHubDispatcher(apply);
  const onGesture = () => hub.dispatch({ type: "gesture" });

  function apply(fx: HubEffect) {
    switch (fx) {
      case "create":
        if (ctx) return;
        ctx = deps.makeContext();
        if (ctx) for (const cb of [...clients]) cb(ctx);
        break;
      // Always ask, even when the state already looks right: a suspend() or
      // resume() still in flight would otherwise win, and they settle in order.
      case "resume":
        if (ctx && ctx.state !== "closed") void ctx.resume().catch(() => {});
        break;
      case "suspend":
        if (ctx && ctx.state !== "closed") void ctx.suspend().catch(() => {});
        break;
      case "unlockBook":
        deps.unlockAudio();
        break;
      case "listen":
        deps.listen(true, onGesture);
        if (deps.isMuted() !== hub.state.muted) hub.dispatch({ type: "mute", on: deps.isMuted() });
        unsubscribeMute = deps.subscribeMuted((m) => hub.dispatch({ type: "mute", on: m }));
        break;
      case "unlisten":
        deps.listen(false, onGesture);
        unsubscribeMute?.();
        unsubscribeMute = null;
        break;
    }
  }

  return {
    context: () => ctx,
    attach(cb) {
      let attached = true;
      if (ctx) cb(ctx);
      else clients.add(cb);
      hub.dispatch({ type: "attach" });
      return () => {
        if (!attached) return;
        attached = false;
        clients.delete(cb);
        hub.dispatch({ type: "detach" });
      };
    },
    wake(owner) {
      const had = awake.size > 0;
      awake.add(owner);
      if (!had) hub.dispatch({ type: "awake", on: true });
      // Another engine already holds it awake: still ask, in case it is interrupted.
      else if (ctx && ctx.state !== "running" && ctx.state !== "closed") void ctx.resume().catch(() => {});
    },
    sleep(owner) {
      if (!awake.delete(owner)) return;
      if (awake.size === 0) hub.dispatch({ type: "awake", on: false });
    },
    awake: () => awake.size > 0,
    state: () => hub.state,
  };
}

