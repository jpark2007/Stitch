/**
 * Socket resolution for held and stacked props.
 *
 * A prop whose `parent` is "puppet:<id>.<socket>" or "prop:<id>.<socket>" asks
 * a SocketResolver where that socket is this frame. The Puppet component (built
 * separately) provides puppet sockets; a PropRegistry provides prop sockets
 * (car seats, a tabletop) from the PropActors registered with it, and falls
 * back to the puppet resolver for everything else.
 *
 * All matrices are in STAGE SPACE: the coordinate space of CONTRACTS.md "Stage
 * space" (x -1..1 across the stage, y up from the ground, z toward the viewer),
 * which is the space of the group the PropActors are mounted in. If the stage
 * is drawn inside a transformed group (on the book's page), convert scene
 * matrices with `toStageSpace` before returning them.
 */

import { Matrix4, type Object3D } from "three";

/**
 * Where a socket is, right now, in stage space.
 *
 * @param target  "puppet:<id>" or "prop:<id>" (the part of `parent` before the dot)
 * @param socket  "hand_r", "hand_l", "head", or a prop socket like "driver"
 * @param out     write the socket's matrix here: its translation is the socket
 *                point; its rotation is the socket's orientation (used only
 *                with heldOrientation="socket"); a negative determinant means
 *                mirrored (a puppet facing left). Scale is ignored.
 * @returns       false if the target or socket is unknown (the prop then
 *                holds its last placement, or stays hidden if it never had one)
 *
 * Must be current for THIS frame. Either compute the socket on demand, or make
 * sure the puppets update before the props (useFrame with a lower priority
 * number, e.g. -1; PropActor uses 0). Called from useFrame: do not allocate.
 */
export type SocketResolver = (target: string, socket: string, out: Matrix4) => boolean;

/** What a PropActor registers so other props and puppets can use its sockets. */
export interface PropSocketSource {
  /** Bring the prop up to date for this frame, then write the named socket's
   *  stage-space matrix into `out`. False if the prop has no such socket or
   *  is not placed. */
  socketMatrix(socket: string, out: Matrix4): boolean;
}

/**
 * The props on one stage, by id, for "prop:<id>.<socket>" parents. Create one
 * per stage (useMemo) and pass it to every PropActor; give the Puppet
 * component `registry.resolve` too so puppets can sit in a car's seat.
 */
export class PropRegistry {
  private readonly props = new Map<string, PropSocketSource>();
  private readonly resolving = new Set<string>();

  /**
   * @param fallback resolver for targets that are not registered props,
   *                 normally the puppets'. Settable later via `setFallback`.
   */
  constructor(private fallback: SocketResolver | null = null) {}

  setFallback(fallback: SocketResolver | null) {
    this.fallback = fallback;
  }

  /** Register a prop by id. Returns the unregister function. */
  register(id: string, source: PropSocketSource): () => void {
    this.props.set(id, source);
    return () => {
      if (this.props.get(id) === source) this.props.delete(id);
    };
  }

  has(id: string): boolean {
    return this.props.has(id);
  }

  /** A SocketResolver over the registered props, then the fallback. Bound, so
   *  it can be passed around as a plain function. */
  readonly resolve: SocketResolver = (target, socket, out) => {
    if (target.startsWith("prop:")) {
      const id = target.slice(5);
      const src = this.props.get(id);
      if (src) {
        // A prop resting on itself, or a loop of props on each other, has no
        // answer: stop instead of recursing forever.
        if (this.resolving.has(id)) return false;
        this.resolving.add(id);
        try {
          return src.socketMatrix(socket, out);
        } finally {
          this.resolving.delete(id);
        }
      }
    }
    return this.fallback ? this.fallback(target, socket, out) : false;
  };
}

const _inv = new Matrix4();

/**
 * The matrix of `object` in the space of `stageRoot` (the group the stage is
 * drawn in), freshly computed even mid-frame. For resolvers that have an
 * Object3D at each socket. Writes into `out`.
 */
export function toStageSpace(object: Object3D, stageRoot: Object3D | null, out: Matrix4): Matrix4 {
  object.updateWorldMatrix(true, false);
  if (!stageRoot) return out.copy(object.matrixWorld);
  stageRoot.updateWorldMatrix(true, false);
  _inv.copy(stageRoot.matrixWorld).invert();
  return out.multiplyMatrices(_inv, object.matrixWorld);
}
