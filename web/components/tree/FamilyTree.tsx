import Link from "next/link";
import type { CSSProperties } from "react";
import { frameWidths, type FrameLimits } from "./frameSize";
import { DEFAULT_LAYOUT, layoutFamily, type TreeLayout } from "./layout";
import { NAME_MIN, NAME_SIZE, nameExtra, nameFit } from "./nameFit";
import { Puppet, PuppetDefs } from "./Puppet";
import { hashSeed, wobblyCubic, wobblyLine, type Pt } from "./sketch";
import type { TreePerson } from "./treeData";
import "./tree.css";

export type { TreePerson } from "./treeData";

/**
 * The home-screen family tree: grandparents on top, children below, couples
 * holding a thread between them, and a stitched thread from each couple down
 * to each of their children. Layout is automatic from the links.
 *
 * Server-safe, no client JS of its own: hover and the draw-in are CSS only.
 */

/** Size limits (maxHeight, minScale, maxScale) are documented on FrameLimits. */
export interface FamilyTreeProps extends FrameLimits {
  people: readonly TreePerson[];
  /** Where a person's node links to (their book). */
  hrefFor?: (personId: string) => string;
  /** A paper placeholder of the tree while the real one loads: no names, no links. */
  ghost?: boolean;
  className?: string;
}

// Node geometry, in layout units, relative to a person's (x, top).
const FIG_W = 96;
const FIG_H = 124.8;
const NODE_H = FIG_H + 40;
/** Where the puppet's hands are, so couples can hold the thread. */
const HAND_DX = ((71 - 50) / 100) * FIG_W;
const HAND_Y = (81 / 130) * FIG_H;
/** Just above the head, where a child's thread ends. */
const HEAD_TOP = (9 / 130) * FIG_H;

const STITCH = "6 4.6";
const GHOST_COLOR = "#d6c6a8";


interface Thread {
  key: string;
  d: string;
  color: string;
  delay: number;
  /** People this thread belongs to (for hover highlighting). */
  ids: string[];
}

function buildThreads(layout: TreeLayout, people: Map<string, TreePerson>, ghost: boolean) {
  const threads: Thread[] = [];
  const knots: { key: string; at: Pt; delay: number; ids: string[] }[] = [];
  const colorOf = (id: string) => (ghost ? GHOST_COLOR : (people.get(id)?.character.color ?? "#8f8173"));
  const extraFor = (id: string) => (ghost ? 0 : nameExtra(people.get(id)?.name ?? ""));

  const coupleKnot = new Map<string, Pt>();
  for (const c of layout.couples) {
    const a = layout.byId.get(c.a)!;
    const b = layout.byId.get(c.b)!;
    const y = a.y + HAND_Y;
    const left: Pt = [a.x + HAND_DX, y];
    const right: Pt = [b.x - HAND_DX, y];
    const mid: Pt = [(left[0] + right[0]) / 2, y + 7];
    const sag = 5;
    const delay = 0.35 + a.gen * 0.45;
    // Each partner holds their own colour; the two meet in a knot.
    threads.push({
      key: `couple-${c.a}-${c.b}-a`,
      d: wobblyCubic(left, [left[0] + 12, y + sag], [mid[0] - 12, mid[1]], mid, { seed: hashSeed(c.a), amplitude: 0.8 }),
      color: colorOf(c.a),
      delay,
      ids: [c.a, c.b],
    });
    threads.push({
      key: `couple-${c.a}-${c.b}-b`,
      d: wobblyCubic(right, [right[0] - 12, y + sag], [mid[0] + 12, mid[1]], mid, { seed: hashSeed(c.b), amplitude: 0.8 }),
      color: colorOf(c.b),
      delay,
      ids: [c.a, c.b],
    });
    knots.push({ key: `${c.a}-${c.b}`, at: mid, delay: delay + 0.5, ids: [c.a, c.b] });
    coupleKnot.set([c.a, c.b].sort().join("+"), mid);
  }

  for (const fam of layout.families) {
    const parents = fam.parentIds.map((id) => layout.byId.get(id)!);
    const lowest = Math.max(...parents.map((p) => p.y));
    const knot = coupleKnot.get([...fam.parentIds].sort().join("+"));
    // A lone parent's thread starts under their name (which may run to two lines).
    const start: Pt = knot ?? [fam.anchorX, lowest + NODE_H - 4 + Math.max(...fam.parentIds.map(extraFor))];
    // A short shared trunk drops between the parents' names, then each
    // child's strand branches off toward them.
    const branchY = Math.max(lowest + NODE_H + 2, start[1] + 8);
    const n = fam.childIds.length;
    fam.childIds.forEach((childId, i) => {
      const child = layout.byId.get(childId)!;
      const seed = hashSeed(childId);
      const ox = (i - (n - 1) / 2) * 2.2;
      const fork: Pt = [start[0] + ox, branchY];
      const end: Pt = [child.x, child.y + HEAD_TOP - 6];
      const dy = end[1] - fork[1];
      const trunk = wobblyLine([start[0] + ox * 0.3, start[1]], fork, { seed, amplitude: 1.2 });
      const branch = wobblyCubic(fork, [fork[0], fork[1] + dy * 0.75], [end[0], end[1] - dy * 0.75], end, {
        seed: seed + 1,
        amplitude: 2.2,
        samples: 16,
      });
      threads.push({
        key: `child-${childId}`,
        // Continue the trunk into the branch as one path (drop the second M).
        d: `${trunk} ${branch.replace(/^M[^C]*/, "")}`,
        color: colorOf(childId),
        delay: 0.6 + parents[0].gen * 0.45,
        ids: [childId, ...fam.parentIds],
      });
      knots.push({ key: `end-${childId}`, at: end, delay: 1.3 + parents[0].gen * 0.45, ids: [childId] });
    });
  }
  return { threads, knots };
}

/** A person's name in their own hand, sized (and broken) to sit under them. */
function NameLabel({ name }: { name: string }) {
  const { lines, size } = nameFit(name);
  const style = size === NAME_SIZE ? undefined : { fontSize: `calc(var(--u) * ${size})` };
  return (
    <span className="ft-name" data-lines={lines.length} data-tight={size <= NAME_MIN ? "" : undefined} style={style}>
      {lines.map((line, i) => (
        <span key={i} className="ft-name-line">
          {line}
        </span>
      ))}
    </span>
  );
}

/** Ids safe to drop into a CSS attribute selector and an SVG url(#...). */
const SAFE_ID = /^[\w-]+$/;

/**
 * Hovering a person fades every thread that isn't theirs, so their partner,
 * parents and children stand out. CSS only: one :has() rule per person.
 */
function highlightRules(ids: string[]): string {
  const safe = ids.filter((id) => SAFE_ID.test(id));
  const rules = safe.map(
    (id) => `.ft-tree:has(.ft-node[data-id="${id}"]:hover) .ft-thread:not([data-ids~="${id}"]){opacity:.18}`,
  );
  return `@media (hover:hover){${rules.join("")}}`;
}

const defaultHref = (id: string) => `/people/${encodeURIComponent(id)}`;

export function FamilyTree({ people, maxHeight, minScale, maxScale, hrefFor = defaultHref, ghost = false, className }: FamilyTreeProps) {
  const layout = layoutFamily(people, DEFAULT_LAYOUT);
  if (!layout.people.length) return null;
  const byId = new Map(people.map((p) => [p.id, p]));
  const W = layout.width;
  // Room under the bottom row for a name that takes two lines.
  const bottomExtra = ghost
    ? 0
    : Math.max(0, ...layout.people.filter((p) => p.gen === layout.rows - 1).map((p) => nameExtra(byId.get(p.id)!.name)));
  const H = layout.height + NODE_H - DEFAULT_LAYOUT.margin + bottomExtra;
  const { threads, knots } = buildThreads(layout, byId, ghost);
  // Mask ids must be unique on the page and valid inside url(#...).
  const maskId = (key: string, i: number) => `tree-draw-${i}-${SAFE_ID.test(key) ? key : hashSeed(key)}`;

  const frameStyle = {
    "--u": `calc(100cqw / ${W.toFixed(3)})`,
    ...frameWidths(W, H, { maxHeight, minScale, maxScale }),
  } as CSSProperties;

  return (
    <div className={`ft-scroll ${ghost ? "ft-ghost" : ""} ${className ?? ""}`}>
      <div className="ft-frame" style={frameStyle}>
        <PuppetDefs />
        {!ghost && <style>{highlightRules(layout.people.map((p) => p.id))}</style>}
        <div className="ft-tree" style={{ aspectRatio: `${W.toFixed(2)} / ${H.toFixed(2)}` }}>
          <svg className="ft-lines" viewBox={`0 0 ${W.toFixed(2)} ${H.toFixed(2)}`} aria-hidden>
            <defs>
              {threads.map((t, i) => (
                <mask key={t.key} id={maskId(t.key, i)} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
                  <path
                    d={t.d}
                    pathLength={1}
                    fill="none"
                    stroke="#fff"
                    strokeWidth="10"
                    strokeLinecap="round"
                    className="ft-draw"
                    style={{ animationDelay: `${t.delay}s` }}
                  />
                </mask>
              ))}
            </defs>
            {threads.map((t, i) => (
              <g
                key={t.key}
                className="ft-thread"
                data-ids={t.ids.join(" ")}
                mask={`url(#${maskId(t.key, i)})`}
                fill="none"
                strokeLinecap="round"
              >
                {/* faint pencil guide, so the stitches read as one line */}
                <path d={t.d} stroke="#5c4e43" strokeOpacity="0.16" strokeWidth="1" />
                {/* stitch shadow, then the thread itself */}
                <path d={t.d} stroke="#4a2f1d" strokeOpacity="0.22" strokeWidth="2.2" strokeDasharray={STITCH} transform="translate(0.5 0.9)" />
                <path d={t.d} stroke={t.color} strokeWidth="2.2" strokeDasharray={STITCH} />
              </g>
            ))}
            {knots.map((k) => (
              <circle
                key={k.key}
                cx={k.at[0]}
                cy={k.at[1]}
                r="2.4"
                fill="#5c4e43"
                fillOpacity="0.55"
                className="ft-knot ft-thread"
                data-ids={k.ids.join(" ")}
                style={{ animationDelay: `${k.delay}s` }}
              />
            ))}
          </svg>

          {layout.people.map((p, i) => {
            const person = byId.get(p.id)!;
            const style = {
              left: `${((p.x / W) * 100).toFixed(3)}%`,
              top: `${((p.y / H) * 100).toFixed(3)}%`,
              animationDelay: `${(p.gen * 0.45 + i * 0.04).toFixed(2)}s`,
            };
            const figure = (
              <span className="ft-figure">
                <span className="ft-ground" aria-hidden />
                <Puppet
                  id={p.id}
                  color={ghost ? GHOST_COLOR : person.character.color}
                  item={ghost ? undefined : (person.character.item ?? undefined)}
                  build={person.character.build ?? undefined}
                  className="ft-puppet"
                />
              </span>
            );
            if (ghost) {
              return (
                <span key={p.id} className="ft-node" style={style} aria-hidden>
                  {figure}
                  <span className="ft-name-ghost" />
                </span>
              );
            }
            return (
              <Link
                key={p.id}
                href={hrefFor(p.id)}
                className="ft-node"
                data-id={p.id}
                style={style}
                aria-label={`${person.name}: open their book`}
              >
                {figure}
                <NameLabel name={person.name} />
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
