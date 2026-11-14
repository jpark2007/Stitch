/**
 * The props the lab shows. The hymn books come straight from the reference
 * sample (art null, so they draw as a placeholder card); the rest are shaped
 * like the prop master's output (ARCHITECTURE.md section 1, "Rig definition").
 */

import memory from "@/public/samples/hymn_books.memory.json";
import type { Prop } from "@/lib/types";
import { CAP_H, CAP_W, CAR_H, CAR_W, CAR_WHEELS } from "./labArt";

export const hymnBooks = (memory.props as Prop[]).find((p) => p.id === "hymn_books")!;

/** One placeholder per size class, as the agent would emit before art exists. */
export const placeholders: Prop[] = [
  {
    id: "wedding_ring",
    name: "wedding ring",
    size: "tiny",
    art: { body: null },
    color: "#d3a03d",
    aspect: 1,
    anchor: [0.5, 1],
    grip: [0.5, 0.5],
  },
  {
    id: "picnic_basket",
    name: "picnic basket",
    size: "carried",
    art: { body: null },
    color: "#b98a4e",
    aspect: 1.25,
    anchor: [0.5, 1],
    grip: [0.5, 0.05],
  },
  {
    id: "kitchen_table",
    name: "kitchen table",
    size: "furniture",
    art: { body: null },
    color: "#a8794f",
    aspect: 1.6,
    anchor: [0.5, 1],
    grip: null,
    sockets: { top: [0.5, 0.14] },
  },
  {
    id: "blue_buick",
    name: "blue Buick",
    size: "vehicle",
    art: { body: null },
    color: "#5b82a6",
    aspect: 2.1,
    anchor: [0.5, 1],
    grip: null,
  },
  {
    id: "corner_store",
    name: "the corner store",
    size: "building",
    art: { body: null },
    color: "#c46a49",
    aspect: 1,
    anchor: [0.5, 1],
    grip: null,
  },
];

/** A car with real (lab-drawn) art and two spinning wheel parts. */
export const greenSedan: Prop = {
  id: "green_sedan_1985",
  name: "the green sedan",
  source: "generated",
  size: "vehicle",
  art: { body: "lab/car_body", wheel: "lab/car_wheel" },
  aspect: CAR_W / CAR_H,
  anchor: [0.5, 0.97],
  grip: null,
  sockets: { driver: [0.6, 0.45], passenger: [0.4, 0.45], roof: [0.5, 0.09] },
  parts: {
    wheel_back: { art: "wheel", at: [...CAR_WHEELS.wheel_back], spin: true },
    wheel_front: { art: "wheel", at: [...CAR_WHEELS.wheel_front], spin: true },
  },
  states: { parked: {}, driving: {} },
};

/** A suitcase with two states drawn as two images. */
export const suitcase: Prop = {
  id: "suitcase",
  name: "grandma's suitcase",
  source: "generated",
  size: "carried",
  art: { body: "lab/case_closed" },
  aspect: 1,
  anchor: [0.5, 0.98],
  grip: [0.5, 0.3],
  states: { closed: {}, open: { body: "lab/case_open" } },
};

/** Dad's flat cap, the signature item put_on puts on a head. The anchor is
 *  inside the crown, above the band (it rests on the head socket when worn,
 *  so the band comes down over the head); the grip is off-centre, pinching
 *  the crown, so a wrong pivot would show sideways too. */
export const flatCap: Prop = {
  id: "flat_cap",
  name: "dad's flat cap",
  source: "generated",
  size: "hand",
  art: { body: "lab/flat_cap" },
  aspect: CAP_W / CAP_H,
  anchor: [0.36, 0.62],
  grip: [0.55, 0.3],
};
