"use client";

// Wraps a signed-in page: asks the server who is signed in, sends visitors who
// are not to the login page (and back here afterwards), optionally sends
// people without a family to start one, and hands the session to the page
// through context or a render function.
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { isApiError, type StitchApi } from "@/lib/api";
import type { MeResponse } from "@/lib/types";
import { Button } from "./Button";
import { getApi } from "./client";
import { FlowFrame } from "./FlowPage";
import { FormError } from "./FormError";
import { PaperCard } from "./PaperCard";
import { forgetFamily, forgetSignedIn, hrefs, rememberSignedIn } from "./session";
import { clearAskCache } from "./askPage";

/** window.sessionStorage, or null where it is missing or blocked. */
function tabStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export interface SessionValue {
  me: MeResponse;
  api: StitchApi;
  /** Ask the server again (after joining or creating a family, say). */
  refresh(): Promise<void>;
  /**
   * Log out, forget the remembered family and load the login page afresh (a
   * full document load, so no page of this session lingers). Resolves as the
   * browser starts leaving; rejects, staying put, when logging out fails.
   */
  signOut(): Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

/** The session of the nearest SessionGate. Throws outside one. */
export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession() must be called inside a <SessionGate>; it has no session to give here.");
  }
  return value;
}

type Status =
  | { kind: "loading" }
  | { kind: "ready"; me: MeResponse }
  | { kind: "error"; message: string }
  /** On the way to another page: keep the placeholder up until we leave. */
  | { kind: "leaving" };

const FALLBACK_ERROR = "Something went wrong while opening your family's pages. Please try again.";

function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.detail;
  return FALLBACK_ERROR;
}

/** The path and query of this page, for coming back after logging in. */
function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

export function SessionGate(props: {
  children: ReactNode | ((s: SessionValue) => ReactNode);
  /** Send people who belong to no family to start one. */
  requireFamily?: boolean;
  /** Replaces the default placeholder while the session loads. */
  loading?: ReactNode;
  /**
   * The column of the loading and error sheets: the page's own FlowPage width,
   * so the column doesn't jump when the page arrives. Default "narrow".
   */
  width?: "narrow" | "wide";
}) {
  const { children, requireFamily = false, loading, width = "narrow" } = props;
  const router = useRouter();
  const api = getApi();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [retrying, setRetrying] = useState(false);

  /** Apply a fresh MeResponse: redirect when it does not fit this page. */
  const settle = useCallback(
    (me: MeResponse) => {
      rememberSignedIn();
      if (requireFamily && me.families.length === 0) {
        setStatus({ kind: "leaving" });
        router.replace(hrefs.newFamily());
        return;
      }
      setStatus({ kind: "ready", me });
    },
    [requireFamily, router],
  );

  /** A 401 goes to the login page (and back here after); true when handled. */
  const leaveIfSignedOut = useCallback(
    (e: unknown): boolean => {
      if (!(isApiError(e) && e.status === 401)) return false;
      forgetSignedIn();
      setStatus({ kind: "leaving" });
      router.replace(hrefs.login(currentPath()));
      return true;
    },
    [router],
  );

  /** A failed me() on load or retry: signed out, or the error sheet. */
  const fail = useCallback(
    (e: unknown) => {
      if (!leaveIfSignedOut(e)) setStatus({ kind: "error", message: errorMessage(e) });
    },
    [leaveIfSignedOut],
  );

  useEffect(() => {
    // Ignore the answer once this mount is gone (strict mode mounts twice).
    let live = true;
    api.me().then(
      (me) => {
        if (live) settle(me);
      },
      (e: unknown) => {
        if (live) fail(e);
      },
    );
    return () => {
      live = false;
    };
  }, [api, settle, fail]);

  const retry = useCallback(() => {
    setRetrying(true);
    api
      .me()
      .then(settle, fail)
      .finally(() => setRetrying(false));
  }, [api, settle, fail]);

  /** Re-run me(). Signed out goes to login; other failures reject and keep the page as it is. */
  const refresh = useCallback(async () => {
    let me: MeResponse;
    try {
      me = await api.me();
    } catch (e) {
      if (leaveIfSignedOut(e)) return;
      throw e;
    }
    settle(me);
  }, [api, settle, leaveIfSignedOut]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch (e) {
      // Already signed out is fine; anything else (offline, say) is the caller's to show.
      if (!(isApiError(e) && e.status === 401)) throw e;
    }
    forgetFamily();
    forgetSignedIn();
    clearAskCache(tabStorage());
    // A full document load, not router.replace: with cacheComponents on, the
    // pages visited this session stay mounted in hidden Activity trees with
    // their state (a sent log in form, a half-written memory). Reloading
    // drops every one of them, so the next person at this browser starts
    // clean. "leaving" keeps the placeholder up until the new page arrives.
    setStatus({ kind: "leaving" });
    window.location.replace(hrefs.login());
  }, [api]);

  const me = status.kind === "ready" ? status.me : null;
  const value = useMemo<SessionValue | null>(
    () => (me ? { me, api, refresh, signOut } : null),
    [me, api, refresh, signOut],
  );

  if (status.kind === "error") {
    return (
      <GateFrame width={width}>
        <PaperCard className="flex flex-col gap-5">
          <h1 className="font-display text-2xl text-ink sm:text-3xl">We couldn&rsquo;t open this page</h1>
          <FormError message={status.message} />
          <div>
            <Button onClick={retry} busy={retrying} busyLabel="Trying again">
              Try again
            </Button>
          </div>
        </PaperCard>
      </GateFrame>
    );
  }

  if (status.kind === "leaving") return <>{loading ?? <SessionPlaceholder label="One moment…" width={width} />}</>;
  if (!value) return <>{loading ?? <SessionPlaceholder label="Opening your family’s pages…" width={width} />}</>;

  return (
    <SessionContext.Provider value={value}>
      {typeof children === "function" ? children(value) : children}
    </SessionContext.Provider>
  );
}

/**
 * A sheet where a flow page's content starts, under the same "Stitch" mark
 * and in a column as wide as the page's, so nothing jumps when it arrives.
 */
function GateFrame(props: { width: "narrow" | "wide"; children: ReactNode }) {
  return <FlowFrame width={props.width}>{props.children}</FlowFrame>;
}

/** A quiet sheet with a running thread while the session loads. */
function SessionPlaceholder(props: { label: string; width: "narrow" | "wide" }) {
  return (
    <GateFrame width={props.width}>
      <div role="status" aria-busy="true" aria-live="polite">
        <PaperCard className="flex flex-col items-center gap-4 text-center">
          <span
            aria-hidden="true"
            className="block h-0 w-24 border-t-[1.5px] border-dashed border-rule motion-safe:animate-pulse"
          />
          <p className="font-display text-lg text-ink-soft">{props.label}</p>
        </PaperCard>
      </div>
    </GateFrame>
  );
}
