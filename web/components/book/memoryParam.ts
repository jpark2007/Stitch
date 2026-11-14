/**
 * ?memory=<memoryId>: open a book at that memory's spread. The /add flow
 * links back with it, so a memory just told opens on its own page, in a
 * person's book (/people/<id>) and in its own (/memories/<id>) alike.
 *
 *   memorySpread(spreads, "m-steps-1987")        // 3, or null when it is not in the book
 *   openAtMemory(query.initial, spreads, memory) // the view the book starts in
 *
 * The rule: a moment spread whose id is the memory id (BookSpread.id is the
 * memory id; "The end" never matches) opens there, as ?spread=<n>&state=open
 * would, and wins over any ?spread or ?state. ?turn and ?focus are kept. A
 * memory the book does not have, or none, leaves the view as it was: the
 * book opens as it would without ?memory, with no error.
 *
 * Pure, no imports (memoryParam.test.ts).
 */

/** What memorySpread reads of a spread (BookSpread fits). */
export interface MemorySpreadLike {
  id: string;
  kind?: "moment" | "end";
}

/** What openAtMemory changes of a starting view (BookInitial fits). */
export interface MemoryStartLike {
  state?: "closed" | "open";
  spread?: number;
}

/** The 1-based spread of memory `memory` in `spreads`, or null when there is none. */
export function memorySpread(spreads: readonly MemorySpreadLike[], memory: string | null | undefined): number | null {
  const id = memory?.trim();
  if (!id) return null;
  const i = spreads.findIndex((s) => s.kind !== "end" && s.id === id);
  return i < 0 ? null : i + 1;
}

/**
 * The view a book starts in, given ?memory: open at the memory's spread when
 * the book has it, else `initial` itself (the same object, untouched).
 */
export function openAtMemory<T extends MemoryStartLike>(
  initial: T,
  spreads: readonly MemorySpreadLike[],
  memory: string | null | undefined,
): T {
  const n = memorySpread(spreads, memory);
  return n === null ? initial : { ...initial, state: "open", spread: n };
}
