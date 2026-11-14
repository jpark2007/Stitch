"use client";

// The pop-up on the right page of a tradition being written: the kind's own
// paper kit (TraditionStage, as the Book of Traditions stands it), raised
// when a kind is first picked, folded down and the new one raised when the
// kind changes. Mounted in Book3D's stage slot (renderStage).
//
// The book draws on demand. The kit asks for frames while it folds and
// rises, then lets its small things (steam, lights, flames) move a few
// seconds, then holds still: a page lying open beside a form draws nothing.
// Under reduced motion the kit simply stands, and the new one replaces it.
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";

import { TraditionStage } from "@/components/traditions/TraditionStage";
import type { TraditionPerson } from "@/components/traditions/traditionBook";

/** Seconds for a kit to fold flat, and to rise. */
const FOLD_S = 0.3;
const RISE_S = 0.9;
/** Seconds its small things keep moving once it stands. */
const LIFE_S = 3;

export interface DraftKitProps {
  category: string;
  variant: number;
  keepers: readonly TraditionPerson[];
  /** The book's reveal and stage view (StageSlotArgs). */
  reveal: { readonly current: number };
  view: { readonly current: number };
  size: readonly [number, number];
  reduced: boolean;
}

export function DraftKit({ category, variant, keepers, reveal, view, size, reduced }: DraftKitProps) {
  const asked = `${category}#${variant}`;
  const [shown, setShown] = useState({ key: asked, category, variant });
  // Under reduced motion the new kit stands at once.
  if (reduced && shown.key !== asked) setShown({ key: asked, category, variant });
  const askedRef = useRef({ key: asked, category, variant });
  useEffect(() => {
    askedRef.current = { key: asked, category, variant };
  });

  // How far the kit itself stands, 0..1, on top of the book's own reveal.
  const rise = useRef(reduced ? 1 : 0);
  const life = useRef(0);
  const shownKey = useRef(shown.key);
  useEffect(() => {
    shownKey.current = shown.key;
  }, [shown.key]);

  const ownReveal = useMemo(
    () => ({
      get current() {
        return reveal.current * rise.current;
      },
    }),
    [reveal],
  );
  // Its small things move only while there is life left in them.
  const ownView = useMemo(
    () => ({
      get current() {
        return life.current > 0 ? view.current : 0;
      },
    }),
    [view],
  );

  const invalidate = useThree((s) => s.invalidate);
  // A change (a kind, the people) shows in the next frame.
  useEffect(() => {
    invalidate();
  }, [invalidate, asked, shown.key, keepers]);
  // Someone ticked or unticked: the people standing sway a moment.
  useEffect(() => {
    if (!reduced && rise.current === 1) life.current = LIFE_S;
  }, [keepers, reduced]);

  useFrame((_, delta) => {
    // A slow frame moves it on as far as the time says; the first frame after a still spell does not jump.
    const dt = Math.min(delta, 1 / 10);    if (reduced) {
      rise.current = 1;
      life.current = 0;
      return;
    }
    let moving = true;
    if (shownKey.current !== askedRef.current.key) {
      // Fold the old kit down; once flat, the new one takes its place.
      rise.current = Math.max(0, rise.current - dt / FOLD_S);
      if (rise.current === 0) {
        shownKey.current = askedRef.current.key;
        setShown(askedRef.current);
      }
    } else if (rise.current < 1) {
      rise.current = Math.min(1, rise.current + dt / RISE_S);
      if (rise.current === 1) life.current = LIFE_S;
    } else if (life.current > 0) {
      life.current = Math.max(0, life.current - dt);
      // TraditionStage asks for its own frames while it lives.
      moving = false;
    } else moving = false;
    if (moving) invalidate();
  });

  return (
    <TraditionStage
      category={shown.category}
      variant={shown.variant}
      keepers={keepers}
      reveal={ownReveal}
      view={ownView}
      size={size}
    />
  );
}
