// Which layers a flow Button renders. Pure, so node --test can check it
// (components/flow/__tests__/buttonLayers.test.mjs).
//
// A button that can be busy keeps its label and its busy label (a spinner and
// `busyLabel`, or the children again) in one grid cell, so it is as wide as the
// wider of the two and never jumps when it goes busy. That hidden layer costs
// width: a button that is never busy would size to it and its label would sit
// off-centre, and a quiet button pulled flush with -ml-3 would start to the
// right of the fields. So only a button whose caller opts into busy behaviour
// (passes `busy`, true or false, or a `busyLabel`) reserves the room.

/** "reserve": both layers, sized to the wider. "none": the children alone. */
export type BusyLayers = "none" | "reserve";

export function busyLayers(p: { busy?: boolean; busyLabel?: string }): BusyLayers {
  return p.busy !== undefined || p.busyLabel !== undefined ? "reserve" : "none";
}
