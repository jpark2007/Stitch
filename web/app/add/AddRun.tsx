"use client";

// A run under way on /add: the slip of what was sent, then the live agent
// feed with its pause cards (follow-up, props, merge). Reached right after
// sending the form, or straight from a `?run=` URL after a reload.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

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
  /** One quiet line naming the family, for someone in more than one. */
  lede?: ReactNode;
  /** After the run's own error: bring the form back with what was sent. */
  onTellAgain?: () => void;
}) {
  const { api, fid, run, lede, onTellAgain } = props;
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
        lede={lede}
        back={{ href: hrefs.home(), label: "Family tree" }}
        width="wide"
      >
        {gone ? (
          // Someone else's run, a run from before a restart, or a mistyped
          // link all land here: the server answers 404 for each alike.
          <PaperCard className="flex flex-col items-start gap-3">
            <div role="status" className="flex flex-col gap-2">
              <h2 className="font-display text-[1.375rem] leading-tight text-ink">
                We can&rsquo;t find this one any more
              </h2>
              <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
                It may have finished a while ago, or the studio restarted. If its page was made,
                it&rsquo;s in the family tree; if not, you can tell the story again.
              </p>
            </div>
            <div className="mt-2 flex flex-wrap gap-3">
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
            onTellAgain={onTellAgain}
          />
        )}
      </FlowPage>
    </div>
  );
}
