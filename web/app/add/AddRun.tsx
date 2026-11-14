"use client";

// A run under way on /add: the slip of what was sent, then the live agent
// feed with its pause cards (follow-up, props, merge). Reached right after
// sending the form, or straight from a `?run=` URL after a reload.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ButtonLink } from "@/components/flow/Button";
import { makeRunSource, RUN_OPTIONS } from "@/components/flow/client";
import { RUN_GONE_MESSAGE, withRunGone } from "@/components/flow/addPage";
import { FlowPage } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
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

  // A run the server no longer knows (a stale ?run= link) gets its own way
  // forward instead of the feed, whose Retry could only meet the same 404.
  const [gone, setGone] = useState(false);
  const markGone = useCallback(() => setGone(true), []);

  // One source per run: a new object would reset the feed and reconnect.
  const source = useMemo(
    () => withRunGone(makeRunSource(api, fid, runId), RUN_GONE_MESSAGE, markGone),
    [api, fid, runId, markGone],
  );
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
        {gone ? (
          <PaperCard className="flex flex-col items-start gap-4">
            <p role="status" className="text-[1.0625rem] leading-relaxed text-pretty text-ink">
              {RUN_GONE_MESSAGE}
            </p>
            <p className="text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
              If its scene was finished, you&rsquo;ll find it in the family tree. Otherwise you can tell
              it again.
            </p>
            <div className="flex flex-wrap gap-3">
              <ButtonLink href={hrefs.add({ familyId: fid })}>Add a memory</ButtonLink>
              <ButtonLink variant="secondary" href={hrefs.home()}>
                Family tree
              </ButtonLink>
            </div>
          </PaperCard>
        ) : (
          <MemoryRun
            source={source}
            options={RUN_OPTIONS}
            runKey={`${fid}/${runId}`}
            telling={telling}
            mediaUrl={mediaUrl}
            onUploadPhoto={onUploadPhoto}
            sceneHref={hrefs.memory}
            addAnotherHref={hrefs.add({ familyId: fid })}
          />
        )}
      </FlowPage>
    </div>
  );
}
