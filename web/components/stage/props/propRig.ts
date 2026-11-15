/**
 * PropRig: the three.js side of one prop actor.
 *
 * Builds the card meshes (body + parts, each with a thin card edge behind it),
 * owns their materials and texture references, and places the prop every
 * frame from a mutable PropPose. Nothing here touches React; PropActor mounts
 * it into a <group> and calls `update()` from useFrame.
 *
 * Per frame: one parent lookup, a few matrix multiplies, a material swap only
 * when the state changes. No allocations.
 */

import { Group, Matrix4, Mesh, PlaneGeometry, Vector3, type MeshStandardMaterial } from "three";
import type { Prop } from "@/lib/types";
import { acquireArt, releaseArt, type ArtEntry } from "./artTextures";
import { createCardMaterial } from "./cardMaterial";
import {
  acquirePlaceholder,
  releasePlaceholder,
  VEHICLE_WHEEL_RADIUS,
  type PlaceholderSpec,
} from "./placeholder";
import {
  artFor,
  bodyArtKey,
  DEFAULT_ANCHOR,
  DEFAULT_GRIP,
  fractionOr,
  groundMatrix,
  heldMatrix,
  imageToLocal,
  parseParent,
  propDims,
  propSocketMatrix,
  socketPivot,
  type HeldOrientation,
  type PropDims,
  type PropPose,
  type SocketPivot,
} from "./rig";
import { mirrorForGrip, placeholderLook, rollsOnWheels, tagLabel } from "./silhouette";
import type { SocketResolver } from "./sockets";

export interface PropRigOptions {
  /** `vocabulary.sizes`. */
  sizes: Readonly<Record<string, number>>;
  /** Media id -> URL. Default: the id is already a URL. */
  resolveArt?: (mediaId: string) => string;
  castShadow?: boolean;
  receiveShadow?: boolean;
}

/** Key for "no art: draw the placeholder". Media ids are never empty. */
const PLACEHOLDER = "";
/** Multiplies the art for the card's edge: the same colours, in shade. */
const EDGE_TINT = "#9a8874";

interface Look {
  face: MeshStandardMaterial;
  edge: MeshStandardMaterial;
  /** Loaded art, or null for a placeholder look. */
  art: ArtEntry | null;
  /** Placeholder spec to release on dispose. */
  spec: PlaceholderSpec | null;
  url: string | null;
}

interface Visual {
  /** Part name, or null for the body. */
  part: string | null;
  artKey: string;
  holder: Group;
  face: Mesh;
  edge: Mesh;
  looks: Map<string, Look>;
  current: Look | null;
  /** Part only: spins (a wheel). */
  spin: boolean;
}

export class PropRig {
  readonly dims: PropDims;
  /** The prop's matrix in stage space, as of the last successful update. */
  readonly matrix = new Matrix4();

  pose: PropPose | null = null;
  resolver: SocketResolver | null = null;
  orientation: HeldOrientation = "upright";
  depthBias = 0.012;
  /** Which point of the prop sits on a "prop:<id>.<socket>" parent. Puppet
   *  sockets ignore it: a hand takes the grip, the head the anchor. */
  propSocketPivot: SocketPivot = "anchor";

  private readonly anchor = { x: 0, y: 0 };
  private readonly grip = { x: 0, y: 0 };
  private readonly sockets = new Map<string, { x: number; y: number }>();
  private readonly visuals: Visual[] = [];
  private readonly geometry = new PlaneGeometry(1, 1);
  private readonly socket = new Matrix4();
  private readonly thickness: number;
  private placed = false;
  private disposed = false;

  constructor(
    readonly root: Group,
    readonly prop: Prop,
    private readonly options: PropRigOptions,
  ) {
    const d = (this.dims = propDims(prop, options.sizes));
    const a = fractionOr(prop.anchor, DEFAULT_ANCHOR);
    const g = fractionOr(prop.grip, DEFAULT_GRIP);
    imageToLocal(this.anchor, a[0], a[1], d.width, d.height);
    imageToLocal(this.grip, g[0], g[1], d.width, d.height);
    for (const [name, at] of Object.entries(prop.sockets ?? {})) {
      const f = fractionOr(at, [0.5, 0.5]);
      this.sockets.set(name, imageToLocal({ x: 0, y: 0 }, f[0], f[1], d.width, d.height));
    }
    // Card stock: thicker on big props, never thicker than a hair on tiny ones.
    this.thickness = Math.min(0.006, Math.max(0.0012, d.height * 0.022));

    root.matrixAutoUpdate = false;
    root.visible = false;

    const bodyKey = bodyArtKey(prop);
    this.visuals.push(this.makeVisual(null, bodyKey ?? "body", false, 0, 0, 0));
    const parts = Object.entries(prop.parts ?? {});
    parts.forEach(([name, part], i) => {
      const f = fractionOr(part.at, [0.5, 0.5]);
      const p = imageToLocal({ x: 0, y: 0 }, f[0], f[1], d.width, d.height);
      // Each part sits a card's thickness in front of the one before.
      const z = (this.thickness + 0.0015) * (i + 1);
      this.visuals.push(this.makeVisual(name, part.art, !!part.spin, p.x, p.y, z));
    });
  }

  /* -------------------------------------------------------------------- */
  /* Building                                                              */
  /* -------------------------------------------------------------------- */

  private makeVisual(part: string | null, artKey: string, spin: boolean, x: number, y: number, z: number): Visual {
    const holder = new Group();
    holder.position.set(x, y, z);
    const face = new Mesh(this.geometry);
    face.castShadow = this.options.castShadow ?? true;
    face.receiveShadow = this.options.receiveShadow ?? false;
    const edge = new Mesh(this.geometry);
    // The card's cut edge: a darker copy just behind and a hair lower, so a
    // card seen a little from above shows its thickness.
    edge.position.set(0, -this.thickness * 0.3, -this.thickness);
    holder.add(edge, face);
    holder.name = part ? `part:${part}` : "body";
    face.name = `${this.prop.id}:${holder.name}`;
    this.root.add(holder);

    const visual: Visual = { part, artKey, holder, face, edge, looks: new Map(), current: null, spin };
    // Every look any state can show, built now so a state change is a swap.
    const ids = new Set<string>();
    ids.add(this.prop.art?.[artKey] ?? PLACEHOLDER);
    for (const s of Object.keys(this.prop.states ?? {})) ids.add(artFor(this.prop, artKey, s) ?? PLACEHOLDER);
    for (const id of ids) visual.looks.set(id, id === PLACEHOLDER ? this.placeholderLook(visual) : this.artLook(id));
    return visual;
  }

  private placeholderSpec(v: Pick<Visual, "part" | "spin">): PlaceholderSpec {
    const p = this.prop;
    const spinning = Object.values(p.parts ?? {}).filter((x) => x.spin);
    // The body is cut in the shape the prop's words name (a "red bicycle"
    // rigged as a vehicle is still a bicycle, a bouquet is flowers), else by
    // its size class (a neutral tag or parcel with the prop's name on it).
    const body = v.part === null;
    const look = body ? placeholderLook(p) : { shape: null, bell: false, variant: null };
    return {
      kind: body ? "body" : v.spin ? "wheel" : "piece",
      size: p.size,
      color: p.color ?? "#c4a37a",
      aspect: body ? this.dims.aspect : 1,
      seed: body ? p.id : `${p.id}.${v.part}`,
      wheels: body && rollsOnWheels(look.shape, p.size) && spinning.length === 0,
      shape: look.shape,
      variant: look.variant,
      bell: look.bell,
      hubs: body ? spinning.map((x) => fractionOr(x.at, [0.5, 0.5])) : undefined,
      label: body && look.shape === null ? tagLabel(p.name) : undefined,
      mirror: body && mirrorForGrip(look.shape, p.grip),
    };
  }

  private placeholderLook(v: Pick<Visual, "part" | "spin">): Look {
    const spec = this.placeholderSpec(v);
    const art = acquirePlaceholder(spec);
    const { width, height } = this.partSize(v.part, null);
    return {
      face: createCardMaterial({ map: art.map, alphaMap: art.alphaMap, width, height }),
      edge: createCardMaterial({ map: art.map, alphaMap: art.alphaMap, width, height, tint: EDGE_TINT, grain: 0.4 }),
      art: null,
      spec,
      url: null,
    };
  }

  private artLook(mediaId: string): Look {
    const url = this.options.resolveArt ? this.options.resolveArt(mediaId) : mediaId;
    const art = acquireArt(url);
    const { width, height } = this.dims;
    return {
      face: createCardMaterial({ map: art.texture, alphaMap: art.alpha, width, height }),
      edge: createCardMaterial({ map: art.texture, alphaMap: art.alpha, width, height, tint: EDGE_TINT, grain: 0.4 }),
      art,
      spec: null,
      url,
    };
  }

  /** World size of a visual. The body is the prop's size. A part with art is
   *  drawn at the body art's pixel scale (parts are cut from the same sheet);
   *  without that, a wheel is the placeholder car's wheel and a piece is a
   *  third of the prop's height. */
  private partSize(part: string | null, art: ArtEntry | null): { width: number; height: number } {
    const { width, height } = this.dims;
    if (part === null) return { width, height };
    const body = this.visuals[0]?.current?.art;
    if (art?.ready) {
      const h =
        body?.ready && body.pixelHeight > 0 ? (art.pixelHeight / body.pixelHeight) * height : height * VEHICLE_WHEEL_RADIUS * 2;
      return { width: h * art.aspect, height: h };
    }
    const spin = this.prop.parts?.[part]?.spin;
    const h = spin ? height * VEHICLE_WHEEL_RADIUS * 2 : height / 3;
    return { width: h, height: h };
  }

  /* -------------------------------------------------------------------- */
  /* Per frame                                                             */
  /* -------------------------------------------------------------------- */

  /** True once the body's art (or placeholder) is drawable. */
  get ready(): boolean {
    const look = this.visuals[0]?.current;
    return !!look && (!look.art || look.art.ready || look.art.failed);
  }

  /** Pick each visual's look for the state; false while the body is loading. */
  private applyState(state: string | null): boolean {
    let bodyChanged = false;
    for (let i = 0; i < this.visuals.length; i++) {
      const v = this.visuals[i];
      const id = artFor(this.prop, v.artKey, state) ?? PLACEHOLDER;
      let look = v.looks.get(id) ?? v.looks.get(PLACEHOLDER) ?? null;
      if (look?.art?.failed) {
        // Art that will not load: fall back to the placeholder, once.
        let ph = v.looks.get(PLACEHOLDER);
        if (!ph) v.looks.set(PLACEHOLDER, (ph = this.placeholderLook(v)));
        look = ph;
      }
      const loading = !look || (!!look.art && !look.art.ready);
      v.holder.visible = !loading;
      // Parts are sized from the body's art, so they wait for it.
      if (i === 0 && loading) return false;
      if (look && !loading && (look !== v.current || bodyChanged)) {
        if (i === 0) bodyChanged = true;
        v.current = look;
        v.face.material = look.face;
        v.edge.material = look.edge;
        const { width, height } = this.partSize(v.part, look.art);
        v.face.scale.set(width, height, 1);
        v.edge.scale.set(width, height, 1);
      }
    }
    return true;
  }

  /**
   * Place the prop from `this.pose`. Returns false when it cannot be placed
   * (its parent socket is unknown, or the body art is still loading); it then
   * keeps its last placement, or stays hidden if it never had one.
   */
  update(): boolean {
    const pose = this.pose;
    if (!pose || this.disposed) return false;
    const drawable = this.applyState(pose.state);

    for (let i = 1; i < this.visuals.length; i++) {
      const v = this.visuals[i];
      v.holder.rotation.z = pose.parts[v.part as string] ?? 0;
    }

    const parent = parseParent(pose.parent);
    let ok = false;
    if (parent.kind === "ground") {
      groundMatrix(this.matrix, pose.position, pose.rotation, this.anchor.x, this.anchor.y);
      ok = true;
    } else if (parent.kind !== "invalid" && this.resolver?.(parent.target, parent.socket, this.socket)) {
      // In a hand: by the grip. On a head (worn) or a prop's socket (a seat,
      // a tabletop): by the anchor, as the compiler places it.
      const pivot = socketPivot(parent, this.propSocketPivot) === "grip" ? this.grip : this.anchor;
      heldMatrix(this.matrix, this.socket, this.orientation, pose.position, pose.rotation, pivot.x, pivot.y);
      // In front of the puppet (or the surface) it is on, not inside it.
      this.matrix.elements[14] += parent.kind === "puppet" ? this.depthBias : this.depthBias * 0.5;
      ok = true;
    }
    if (ok) {
      this.placed = true;
      this.root.matrix.copy(this.matrix);
      this.root.matrixWorldNeedsUpdate = true;
    }
    this.root.visible = this.placed && drawable;
    return ok && drawable;
  }

  /** A socket on this prop (`prop.sockets`), in stage space, current. */
  socketMatrix(name: string, out: Matrix4): boolean {
    const p = this.sockets.get(name);
    if (!p) return false;
    this.update();
    if (!this.placed) return false;
    propSocketMatrix(out, this.matrix, p.x, p.y);
    return true;
  }

  /** Where an image fraction of the prop is in stage space (e.g. its anchor,
   *  to drop it where it is). */
  pointAt(u: number, v: number, out: Vector3): Vector3 {
    const { width, height } = this.dims;
    return out.set((u - 0.5) * width, (0.5 - v) * height, 0).applyMatrix4(this.matrix);
  }

  /* -------------------------------------------------------------------- */
  /* Teardown                                                              */
  /* -------------------------------------------------------------------- */

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const v of this.visuals) {
      this.root.remove(v.holder);
      for (const look of v.looks.values()) {
        look.face.dispose();
        look.edge.dispose();
        if (look.url) releaseArt(look.url);
        if (look.spec) releasePlaceholder(look.spec);
      }
      v.looks.clear();
    }
    this.geometry.dispose();
  }
}
