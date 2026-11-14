import Link from "next/link";
import type { CSSProperties } from "react";
import { DEFAULT_LAYOUT, layoutFamily, type TreeLayout } from "./layout";
import { Puppet, PuppetDefs, type Build } from "./Puppet";
import { hashSeed, wobblyCubic, wobblyLine, type Pt } from "./sketch";
import "./tree.css";

/**
 * The home-screen family tree: grandparents on top, children below, couples
 * holding a thread between them, and a stitched thread from each couple down
 * to each of their children. Layout is automatic from the links.
 *
 * Server component, no client JS: hover and the draw-in are CSS only.
 */

/** The bits of a person the tree reads (subset of ARCHITECTURE.md `people`). */
export interface TreePerson {
  id: string;
  name: string;
  parentIds: string[];
  spouseIds: string[];
  character: { color: string; item?: string; build?: Build };
}

export interface FamilyTreeProps {
  people: readonly TreePerson[];
  /** Cap the tree's height (any CSS length); it then narrows to keep its shape. */
  maxHeight?: string;
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

interface Thread {
  key: string;
  d: string;
  color: string;
  delay: number;
}

function buildThreads(layout: TreeLayout, people: Map<string, TreePerson>) {
  const threads: Thread[] = [];
  const knots: { key: string; at: Pt; delay: number }[] = [];
  const colorOf = (id: string) => people.get(id)?.character.color ?? "#8f8173";

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
      key: `couple-${c.a}`,
      d: wobblyCubic(left, [left[0] + 12, y + sag], [mid[0] - 12, mid[1]], mid, { seed: hashSeed(c.a), amplitude: 0.8 }),
      color: colorOf(c.a),
      delay,
    });
    threads.push({
      key: `couple-${c.b}`,
      d: wobblyCubic(right, [right[0] - 12, y + sag], [mid[0] + 12, mid[1]], mid, { seed: hashSeed(c.b), amplitude: 0.8 }),
      color: colorOf(c.b),
      delay,
    });
    knots.push({ key: `${c.a}-${c.b}`, at: mid, delay: delay + 0.5 });
    coupleKnot.set([c.a, c.b].sort().join("+"), mid);
  }

  for (const fam of layout.families) {
    const parents = fam.parentIds.map((id) => layout.byId.get(id)!);
    const start: Pt =
      coupleKnot.get([...fam.parentIds].sort().join("+")) ??
      [fam.anchorX, Math.max(...parents.map((p) => p.y)) + NODE_H - 4];
    // A short shared trunk drops between the parents' names, then each
    // child's strand branches off toward them.
    const branchY = Math.max(...parents.map((p) => p.y)) + NODE_H + 2;
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
      });
      knots.push({ key: `end-${childId}`, at: end, delay: 1.3 + parents[0].gen * 0.45 });
    });
  }
  return { threads, knots };
}

export function FamilyTree({ people, maxHeight, className }: FamilyTreeProps) {
  const layout = layoutFamily(people, DEFAULT_LAYOUT);
  const W = layout.width;
  const H = layout.height + NODE_H - DEFAULT_LAYOUT.margin;
  const byId = new Map(people.map((p) => [p.id, p]));
  const { threads, knots } = buildThreads(layout, byId);

  const frameStyle = {
    "--u": `calc(100cqw / ${W.toFixed(3)})`,
    ...(maxHeight ? { maxWidth: `calc(${maxHeight} * ${(W / H).toFixed(4)})` } : {}),
  } as CSSProperties;

  return (
    <div className={`ft-frame ${className ?? ""}`} style={frameStyle}>
      <PuppetDefs />
      <div className="ft-tree" style={{ aspectRatio: `${W.toFixed(2)} / ${H.toFixed(2)}` }}>
        <svg className="ft-lines" viewBox={`0 0 ${W.toFixed(2)} ${H.toFixed(2)}`} aria-hidden>
          <defs>
            {threads.map((t) => (
              <mask key={t.key} id={`tree-draw-${t.key}`} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
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
          {threads.map((t) => (
            <g key={t.key} mask={`url(#tree-draw-${t.key})`} fill="none" strokeLinecap="round">
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
              className="ft-knot"
              style={{ animationDelay: `${k.delay}s` }}
            />
          ))}
        </svg>

        {layout.people.map((p, i) => {
          const person = byId.get(p.id)!;
          return (
            <Link
              key={p.id}
              href={`/people/${p.id}`}
              className="ft-node"
              style={{
                left: `${((p.x / W) * 100).toFixed(3)}%`,
                top: `${((p.y / H) * 100).toFixed(3)}%`,
                animationDelay: `${(p.gen * 0.45 + i * 0.04).toFixed(2)}s`,
              }}
              aria-label={`${person.name}: open their book`}
            >
              <span className="ft-figure">
                <span className="ft-ground" aria-hidden />
                <Puppet
                  id={p.id}
                  color={person.character.color}
                  item={person.character.item}
                  build={person.character.build}
                  className="ft-puppet"
                />
              </span>
              <span className="ft-name">{person.name}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
