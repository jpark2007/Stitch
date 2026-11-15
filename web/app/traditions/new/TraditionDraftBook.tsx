"use client";

// The family's Book of Traditions beside the "Add a tradition" form, lying
// open at its next blank page (the same desk and book as /add's).
//
// While they write, the story page (left) prints the tradition as the book
// will: the line saying when it happens once "how often" is picked, its
// name, its story, a snapshot of the people who keep it, "since <year>" and
// its first photo, through the book's own builder (traditionBook.ts
// draftSpread), so the preview is the page. Typing reprints it after a pause;
// a click (a kind, how often, a person, a photo) at once. The right page is
// pencilled "the pop-up rises here" until a kind is picked; then that kind's
// paper kit rises in it, and picking another folds it down and raises the
// new one (DraftKit). With nothing written the spread is blank paper.
//
// Once saved, the page is reprinted from the saved tradition and the line
// under the book says it is in the book; the page then goes to the book.
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import type { StageSlotArgs } from "@/components/book/StoryPage";
import { loadPageFonts, type PageFonts } from "@/components/book/paperArt";
import type { BookCover, BookSpread } from "@/components/book/types";
import { BookPanel, TableNote, useDebounced, usePrefersReducedMotion } from "@/components/flow/BookDesk";
import type { Watch } from "@/components/flow/crafting";
import { usePhotoPrints } from "@/components/flow/photoPrints";
import { CATEGORY_OPTIONS, traditionDraftPage, type TraditionDraft } from "@/components/flow/traditionForm";
import { kitFor, kitSpecs } from "@/components/traditions/kitArt";
import { drawKeepersSnapshot } from "@/components/traditions/snapshot";
import {
  DRAFT_TRADITION_ID,
  draftSpread,
  draftTradition,
  draftVariant,
  keepersOf,
  peopleById,
  traditionSpread,
  variantIn,
  type TraditionBookOptions,
  type TraditionPerson,
} from "@/components/traditions/traditionBook";
import type { Person, Tradition } from "@/lib/types";

// three.js needs the browser: the book and its kits load on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <TableNote>opening the book&hellip;</TableNote>,
});
const DraftKit = dynamic(() => import("./DraftKit").then((m) => m.DraftKit), { ssr: false });

/** What the form holds now. */
export interface TraditionInput {
  draft: TraditionDraft;
  photos: File[];
}

export interface TraditionDraftBookProps {
  /** "The Rivera Book of Traditions", and where the book opens. */
  owner: { title: string; href: string };
  cover: BookCover;
  /** The family's people, for the snapshot and the kit's puppets. */
  people: readonly Pick<Person, "id" | "name" | "character">[];
  /** The family's traditions already in the book: which of its kind's kits this one gets. */
  existing: readonly Tradition[];
  selfPersonId: string | null;
  input: Watch<TraditionInput | null>;
  /** The tradition once it is saved; null until then. */
  saved: Watch<Tradition | null>;
}

/** The writing pauses this long before the page reprints. */
const SETTLE_MS = 450;
/** The book opens at its one spread, looking at both pages. */
const OPEN = { state: "open", spread: 1, focus: 0 } as const;
const NO_FILES: File[] = [];

/** A small stable id per object, for telling a new photo list from the old one. */
const ids = new WeakMap<object, number>();
let nextId = 1;
function idOf(o: object): number {
  let id = ids.get(o);
  if (id === undefined) {
    id = nextId++;
    ids.set(o, id);
  }
  return id;
}

/** What a click changes (reprinted at once); typing changes the rest. */
function clicked(input: TraditionInput | null): string {
  if (!input) return "";
  const d = input.draft;
  return `${d.category}|${d.kind}|${d.month}|${d.participantIds.join(",")}|${idOf(input.photos)}`;
}

/** Snapshots drawn so far (a JPEG each): by category, tradition and people. A few are kept. */
const snapshots = new Map<string, string | null>();
function snapshotOf(tradition: Tradition, keepers: readonly TraditionPerson[], fonts: PageFonts): string | null {
  const key = `${tradition.category}|${tradition.id}|${keepers.map((k) => `${k.id}:${k.name}:${k.color}`).join(",")}`;
  if (snapshots.has(key)) return snapshots.get(key) ?? null;
  const url = drawKeepersSnapshot(tradition, keepers, fonts);
  snapshots.set(key, url);
  if (snapshots.size > 12) snapshots.delete(snapshots.keys().next().value as string);
  return url;
}

export function TraditionDraftBook(props: TraditionDraftBookProps) {
  const { owner, cover, people, existing, selfPersonId } = props;
  const reduced = usePrefersReducedMotion();
  const input = useSyncExternalStore(props.input.subscribe, props.input.get, props.input.get);
  const saved = useSyncExternalStore(props.saved.subscribe, props.saved.get, props.saved.get);
  const settled = useDebounced(input, saved ? 0 : SETTLE_MS, clicked(input));

  const [fonts, setFonts] = useState<PageFonts | null>(null);
  useEffect(() => {
    let live = true;
    loadPageFonts().then((f) => {
      if (live) setFonts(f);
    });
    return () => {
      live = false;
    };
  }, []);

  const prints = usePhotoPrints(settled?.photos ?? NO_FILES);
  const photoIds = useMemo(() => prints.map((_, i) => `draft-photo-${i}`), [prints]);
  const known = useMemo(() => peopleById(people), [people]);
  const page = useMemo(
    () => (settled ? traditionDraftPage(settled.draft, selfPersonId, photoIds) : null),
    [settled, selfPersonId, photoIds],
  );

  // The people who keep it, the same array while they are the same people.
  const keeperIds = (saved ? saved.participantIds : (page?.participantIds ?? [])).join(",");
  const keepers = useMemo(
    () => keepersOf({ participantIds: keeperIds ? keeperIds.split(",") : [] }, known),
    [keeperIds, known],
  );

  const category = saved ? saved.category : (page?.category ?? null);
  // The draft's kit, held through the saved beat (draftVariant).
  const variant = useMemo(() => (category ? draftVariant(existing, page, saved) : 0), [existing, page, saved, category]);

  const spread = useMemo<BookSpread | null>(() => {
    const options: TraditionBookOptions = {
      snapshot: fonts ? (t, k) => snapshotOf(t, k, fonts) : undefined,
    };
    if (saved) {
      // The page as saved: its photos are the ones just sent, printed from here.
      options.photoUrl = (id) => prints[saved.photoIds.indexOf(id)]?.url ?? null;
      // Under the draft's spread id still: the book keeps the stage standing
      // in the slot (a new id would mount it afresh, and the kit rise again).
      return { ...traditionSpread(saved, keepers, options), id: DRAFT_TRADITION_ID };
    }
    if (!page) return null;
    options.photoUrl = (id) => prints[photoIds.indexOf(id)]?.url ?? null;
    return draftSpread(page, keepers, options);
  }, [saved, page, keepers, prints, photoIds, fonts]);

  // The book reprints only when the printed page changes.
  const sig = spread ? JSON.stringify(spread) : "";
  const spreads = useMemo<BookSpread[]>(() => (sig ? [JSON.parse(sig) as BookSpread] : [BLANK]), [sig]);

  const renderStage = useCallback(
    (args: StageSlotArgs) =>
      category ? (
        <DraftKit
          category={category}
          variant={variant}
          keepers={keepers}
          reveal={args.reveal}
          view={args.view}
          size={[args.width, args.depth]}
          reduced={reduced}
        />
      ) : null,
    [category, variant, keepers, reduced],
  );

  // Cut every kind's paper in idle time once the book is out, so a kind
  // picked rises without a hitch.
  useEffect(() => {
    let live = true;
    const blank = draftTradition({ ...BLANK_PAGE, startedYear: null });
    const specs = [
      ...new Map(
        CATEGORY_OPTIONS.flatMap(({ value }) =>
          kitSpecs(kitFor(value, variantIn(existing, { ...blank, category: value }))),
        ).map((s) => [s.key, s]),
      ).values(),
    ];
    const t = window.setTimeout(() => {
      import("@/components/stage/cutout")
        .then(async ({ preloadCutouts }) => {
          if (live) await preloadCutouts(specs);
        })
        .catch(() => {
          // Cut when picked instead.
        });
    }, 1200);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [existing]);

  const back = useMemo(() => ({ href: owner.href, label: owner.title }), [owner.href, owner.title]);
  // Built only when what it shows changes, so a keystroke that does not
  // reprint the page does not render (or draw) the book at all.
  const book = useMemo(
    () => <Book3D spreads={spreads} cover={cover} initial={OPEN} renderStage={renderStage} still back={back} />,
    [spreads, cover, renderStage, back],
  );

  return (
    <BookPanel
      stage={saved ? "saved" : "writing"}
      quiet={!saved}
      caption={saved ? "It’s in the book." : "your page fills in as you write"}
    >
      {book}
    </BookPanel>
  );
}

const BLANK_PAGE = {
  title: "",
  description: "",
  category: null,
  when: { kind: null },
  participantIds: [],
  photoIds: [],
  startedYear: null,
} as const;

/** A blank page, before the form has said anything. */
const BLANK: BookSpread = draftSpread(BLANK_PAGE, []);
