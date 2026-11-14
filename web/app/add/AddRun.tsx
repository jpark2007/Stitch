"use client";

// A run under way on /add: the slip of what was sent, then the live agent
// feed with its pause cards (follow-up, props, merge). Reached right after
// sending the form, or straight from a `?run=` URL after a reload.
import { useCallback, useEffect, useMemo, useRef } from "react";

import { makeRunSource, RUN_OPTIONS } from "@/components/flow/client";
import { withRunGone } from "@/components/flow/addPage";
import { FlowPage } from "@/components/flow/FlowPage";
import { MemoryRun, type MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs } from "@/components/flow/session";
import type { StitchApi } from "@/lib/api";

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function AddRun(props: {
  api: StitchApi;
  fid: string;
  run: { runId: string; telling: MemoryRunTelling | null; fresh: boolean };
}) {
  const { api, fid, run } = props;
  const { runId, telling, fresh } = run;

  // One source per run: a new object would reset the feed and reconnect.
  const source = useMemo(() => withRunGone(makeRunSource(api, fid, runId)), [api, fid, runId]);
  const mediaUrl = useCallback((id: string) => api.mediaUrl(fid, id), [api, fid]);
  const onUploadPhoto = useCallback(async (f: File) => (await api.uploadMedia(fid, f)).id, [api, fid]);

  // Just sent from the form below the fold: bring the top of the page into
  // view and move focus to the heading, so the swap is not silent.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!fresh) return;
    window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    const heading = rootRef.current?.querySelector<HTMLElement>("h1");
    if (heading) {
      if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }, [fresh]);

  const second = Boolean(telling?.secondTellingOf);

  return (
    <div ref={rootRef} className="contents">
      <FlowPage
        title={second ? "Your telling" : "Your memory"}
        back={{ href: hrefs.home(), label: "Family tree" }}
        width="wide"
      >
        <MemoryRun
          source={source}
          options={RUN_OPTIONS}
          telling={telling}
          mediaUrl={mediaUrl}
          onUploadPhoto={onUploadPhoto}
          sceneHref={hrefs.memory}
          addAnotherHref={hrefs.add({ familyId: fid })}
        />
      </FlowPage>
    </div>
  );
}
