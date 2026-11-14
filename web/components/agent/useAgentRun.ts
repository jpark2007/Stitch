"use client";

// Glue between a run's event stream and the agent feed: folds the stream into
// a RunState, reconnects after drops, and posts the family's answers. The
// stream and the answer route come in through an injected RunSource, so this
// hook never talks to the API client directly.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import type { AnswerRequest, PropEdit } from "@/lib/types";
import { initialRunState, runReducer, type RunEventMessage, type RunState } from "./runState";
import {
  DEFAULT_MAX_RETRIES,
  answerBody,
  answersStorageKey,
  defaultRetryDelay,
  followRun,
  newRunBook,
  parseAnswers,
  sendAnswer,
  serializeAnswers,
  type AnsweredRecord,
  type RunBook,
} from "./runConnection";

export interface RunSource {
  /** Opens the run's event stream; must end quietly when signal aborts. */
  stream(signal: AbortSignal): AsyncIterable<RunEventMessage>;
  /**
   * POST the answer; rejects with an Error whose message is user-safe. An
   * error with `status` 409 means the run is no longer waiting for it: the
   * hook then closes the pause quietly instead of showing that message.
   */
  answer(body: AnswerRequest): Promise<void>;
}

export interface UseAgentRunOptions {
  /** The server sends the whole run again on every connection (default true, as the mock does). */
  replayOnReconnect?: boolean;
  /** Reconnects after a drop before giving up (default 5). */
  maxRetries?: number;
  /** Wait before reconnect `attempt` (1-based); default 1s doubling to 8s. */
  retryDelayMs?: (attempt: number) => number;
  /**
   * Names the run (family and run id). With it, the answers given in this tab
   * are kept in sessionStorage, so after a reload the replay closes each
   * answered pause where it was answered instead of showing its card again.
   */
  runKey?: string;
}

/** Answers saved for `runKey` in this tab; empty when storage is unavailable. */
function loadAnswers(runKey: string | undefined): AnsweredRecord[] {
  if (!runKey) return [];
  try {
    return parseAnswers(window.sessionStorage.getItem(answersStorageKey(runKey)));
  } catch {
    return [];
  }
}

/** Saves the book's answers for `runKey`; a full or blocked storage just skips it. */
function saveAnswers(runKey: string | undefined, book: RunBook): void {
  if (!runKey) return;
  try {
    window.sessionStorage.setItem(answersStorageKey(runKey), serializeAnswers(book.answers));
  } catch {
    // Without storage a reload shows the card again; answering it then gets
    // a 409, which closes it quietly.
  }
}

interface Session {
  source: RunSource;
  /** The retry() count this session last connected for. */
  nonce: number;
  book: RunBook;
  /** UseAgentRunOptions.runKey when the session started. */
  runKey: string | undefined;
}

/**
 * Follows one agent run. Pass `null` until the run exists; a new source
 * (by identity) resets the feed and connects to it. Options are read when a
 * connection starts, so inline values do not cause reconnects.
 */
export function useAgentRun(
  source: RunSource | null,
  opts?: UseAgentRunOptions,
): {
  state: RunState;
  answerQuestion(text: string): Promise<void>;
  reviewProps(edits: PropEdit[]): Promise<void>;
  decideMerge(approve: boolean): Promise<void>;
  retry(): void;
} {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  const [nonce, setNonce] = useState(0);
  const sessionRef = useRef<Session | null>(null);
  const mountedRef = useRef(false);
  const optsRef = useRef(opts);

  // Declared first so it runs before the connect effect in the same commit.
  useEffect(() => {
    optsRef.current = opts;
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!source) {
      sessionRef.current = null;
      dispatch({ type: "reset" });
      return;
    }
    const o = optsRef.current;
    const replayOnReconnect = o?.replayOnReconnect ?? true;

    let session = sessionRef.current;
    if (!session || session.source !== source) {
      const runKey = o?.runKey;
      session = { source, nonce, book: newRunBook(loadAnswers(runKey)), runKey };
      sessionRef.current = session;
      dispatch({ type: "reset" });
    } else if (session.nonce !== nonce) {
      // retry(): start again from a clean feed when the server will replay it.
      // Without replay the server resumes, so the state so far is kept.
      session.nonce = nonce;
      if (replayOnReconnect) {
        session.book.folded = 0;
        dispatch({ type: "reset" });
      }
    }
    // A re-run with the same source and nonce (StrictMode, Fast Refresh) just
    // reconnects; with replay, followRun resets on the first replayed event.

    const controller = new AbortController();
    const { book } = session;
    const signal = controller.signal;
    void (async () => {
      // StrictMode mounts, unmounts and remounts in one go: wait a microtask so
      // the throwaway first effect is aborted before it ever opens a stream.
      await Promise.resolve();
      if (signal.aborted) return;
      await followRun({
        stream: (s) => source.stream(s),
        book,
        dispatch: (action) => {
          if (!signal.aborted) dispatch(action);
        },
        signal,
        replayOnReconnect,
        maxRetries: o?.maxRetries ?? DEFAULT_MAX_RETRIES,
        retryDelayMs: o?.retryDelayMs ?? defaultRetryDelay,
      });
    })();
    return () => controller.abort();
  }, [source, nonce]);

  const pendingKind = state.pending?.kind ?? null;

  const send = useCallback(
    async (body: AnswerRequest): Promise<void> => {
      if (!source) return;
      // Ignored unless the run is paused on this kind. A 409 (already
      // answered) closes the pause and resolves; any other rejection reaches
      // the card, which shows its message inline.
      await sendAnswer({
        answer: (b) => source.answer(b),
        body,
        pendingKind,
        currentBook: () => {
          const session = sessionRef.current;
          return mountedRef.current && session?.source === source ? session.book : null;
        },
        dispatch,
        onRecorded: (book) => saveAnswers(sessionRef.current?.runKey, book),
      });
    },
    [source, pendingKind],
  );

  const answerQuestion = useCallback((text: string) => send(answerBody("answer", text)), [send]);
  const reviewProps = useCallback((edits: PropEdit[]) => send(answerBody("props", edits)), [send]);
  const decideMerge = useCallback((approve: boolean) => send(answerBody("merge", approve)), [send]);
  const retry = useCallback(() => setNonce((n) => n + 1), []);

  return { state, answerQuestion, reviewProps, decideMerge, retry };
}
