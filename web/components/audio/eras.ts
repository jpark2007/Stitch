/**
 * Year -> era, with the same boundaries as `shared/vocabulary.json` `eras`
 * (eras.test.ts holds the two together). `Memory.era` is derived from the
 * year the same way, so the book can ask for music before the API says so.
 *
 * Pure and dependency-free: Node's test runner imports it directly.
 */

export type EraId =
  | "pre1940"
  | "1940s"
  | "1950s"
  | "1960s"
  | "1970s"
  | "1980s"
  | "1990s"
  | "2000s"
  | "2010s"
  | "2020s";

export interface EraRange {
  id: EraId;
  /** First year, inclusive. */
  from: number;
  /** Last year, inclusive. */
  to: number;
}

export const ERAS: readonly EraRange[] = [
  { id: "pre1940", from: 0, to: 1939 },
  { id: "1940s", from: 1940, to: 1949 },
  { id: "1950s", from: 1950, to: 1959 },
  { id: "1960s", from: 1960, to: 1969 },
  { id: "1970s", from: 1970, to: 1979 },
  { id: "1980s", from: 1980, to: 1989 },
  { id: "1990s", from: 1990, to: 1999 },
  { id: "2000s", from: 2000, to: 2009 },
  { id: "2010s", from: 2010, to: 2019 },
  { id: "2020s", from: 2020, to: 9999 },
];

export const ERA_IDS: readonly EraId[] = ERAS.map((e) => e.id);

/**
 * The era a year falls in. A missing, zero, negative or non-finite year has
 * no era (null); a fractional year counts as the year it starts in.
 */
export function eraForYear(year: number | null | undefined): EraId | null {
  if (typeof year !== "number" || !Number.isFinite(year) || year <= 0) return null;
  const y = Math.floor(year);
  for (const e of ERAS) if (y >= e.from && y <= e.to) return e.id;
  return null;
}

export function isEraId(v: unknown): v is EraId {
  return typeof v === "string" && (ERA_IDS as readonly string[]).includes(v);
}

/** A representative year inside an era, for sliders and demos. */
export function eraMidYear(id: EraId): number {
  const e = ERAS.find((x) => x.id === id)!;
  return id === "pre1940" ? 1930 : id === "2020s" ? 2024 : e.from + 5;
}
