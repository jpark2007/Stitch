"use client";

// Which family a film page is about, as on /add and /ask: `?family=` when it
// is one of this person's, else the remembered family, else their first. The
// family picked is remembered for next time. Also gives the film client
// (filmApi) for the session.
import { useEffect, useMemo } from "react";

import type { SessionValue } from "@/components/flow/SessionGate";
import { familyChoice, type FamilyChoice } from "@/components/flow/addPage";
import { recallFamily, rememberFamily } from "@/components/flow/session";
import type { StitchApi } from "@/lib/api";
import type { FamilySummary } from "@/lib/types";

import { filmApi } from "./filmApi";

export function useFilmFamily(
  session: SessionValue,
  familyId: string | null,
): { choice: FamilyChoice; family: FamilySummary | null; fid: string | null; api: StitchApi } {
  const { me } = session;
  const wanted = familyId ?? recallFamily();
  const choice = useMemo(() => familyChoice(me.families, wanted), [me.families, wanted]);
  const family = choice.current;
  const fid = family?.id ?? null;
  useEffect(() => {
    if (fid) rememberFamily(fid);
  }, [fid]);
  const api = useMemo(() => filmApi(session.api), [session.api]);
  return { choice, family, fid, api };
}
