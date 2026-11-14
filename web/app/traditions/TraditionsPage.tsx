"use client";

/**
 * /traditions: the Book of Family Traditions (components/traditions/
 * TraditionsScreen.tsx has the book, its states and its query).
 *
 *   /traditions?family=<fid>    that family's traditions
 *   /traditions?demo=1          the demo family's, read-only
 */

import { use } from "react";
import type { Search } from "@/components/book/moment/MomentBook";
import { TraditionsScreen } from "@/components/traditions/TraditionsScreen";

export function TraditionsPage({ searchParams }: { searchParams: Promise<Search> }) {
  return <TraditionsScreen search={use(searchParams)} />;
}
