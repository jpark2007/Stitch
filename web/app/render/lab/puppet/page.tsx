import type { Metadata } from "next";
import { Suspense } from "react";
import { PuppetLab } from "./PuppetLab";

export const metadata: Metadata = {
  title: "Puppet lab · Stitch",
  description: "The rigged paper puppet: poses, walk cycle and arm IK.",
};

/**
 * /render/lab/puppet — the paper puppet on a plain paper stage.
 *
 *   ?pose=<name>   start in a pose from shared/rig/poses.json (default stand)
 *   ?walk=0|1      walk the first puppet back and forth (default 0)
 *   ?reach=0|1     the right-hand puppet's hand follows the yarn ball (default 1)
 *   ?t=<seconds>   freeze time (walk phase, idle target) for screenshots
 *   ?crowd=<n>     n puppets walking, for the frame-rate check
 *   ?focus=<i>     close camera on lineup puppet i
 *   ?ui=0          hide the controls
 */
export default function PuppetLabPage() {
  return (
    <Suspense fallback={<main className="h-dvh bg-paper" />}>
      <PuppetLab />
    </Suspense>
  );
}
