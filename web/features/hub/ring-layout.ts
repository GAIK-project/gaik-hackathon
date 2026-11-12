import { CARD_ASPECT } from "./cards";

/**
 * Geometry of the ring, in CSS pixels. The scene uses one world unit per CSS pixel and a
 * camera that shows the plane of the front card at scale 1, so the front card at rest is
 * exactly `cardW` by `cardH` in the middle of the stage. The link that opens the demo is
 * laid over that same rectangle.
 */
export interface RingLayout {
  stageW: number;
  stageH: number;
  cardW: number;
  cardH: number;
  /** Radius of the ring. */
  radius: number;
  /** Camera distance from the front card: smaller is a wider lens. */
  distance: number;
  /** How far the canvas reaches above and below the stage, for the glows. */
  pad: number;
  /** Pointer travel that turns the ring by one card. */
  stepPx: number;
  /** Device pixels per CSS pixel the scene renders at. */
  dpr: number;
  /** Size of the canvas in device pixels. */
  canvasW: number;
  canvasH: number;
}

/** Angle between neighbouring cards as the viewer sees them, radians. */
export const SPREAD = (54 * Math.PI) / 180;
/** How much of its angle a card turns away: 1 would be a true ring, with edge-on sides. */
export const FACING = 0.62;

const MAX_CARD_H = 480;
/** Most of the canvas the browser is asked to fill, in device pixels. */
const PIXEL_BUDGET = 6_500_000;

export function ringLayout(stageW: number, stageH: number, devicePixelRatio: number): RingLayout {
  let cardH = Math.min(stageH * 0.86, MAX_CARD_H);
  let cardW = cardH * CARD_ASPECT;
  const widest = stageW * 0.68;
  if (cardW > widest) {
    cardW = widest;
    cardH = cardW / CARD_ASPECT;
  }
  const pad = Math.round(stageH * 0.45);

  // Render at the screen's own density when it is 1, 1.5, 2 or 3 and the canvas stays
  // inside the budget, so the text on the front card maps one to one to device pixels.
  // Screens in between (many Android phones are 2.6) get 2: their GPUs are the ones that
  // cannot afford the smear at full density.
  const area = stageW * (stageH + 2 * pad);
  const dpr =
    [3, devicePixelRatio, 2, 1.5, 1].find(
      (ratio) =>
        ratio <= devicePixelRatio &&
        (ratio <= 2 || ratio === 3) &&
        area * ratio * ratio <= PIXEL_BUDGET,
    ) ?? 1;

  // The canvas and the card are whole device pixels, and the card sits a whole number of
  // them from the canvas edge: the front card's texture then maps one to one to the screen.
  const canvasW = Math.round(stageW * dpr);
  const canvasH = Math.round((stageH + 2 * pad) * dpr);
  const fit = (size: number, canvas: number) => {
    const pixels = Math.round(size * dpr);
    return (pixels - ((canvas - pixels) % 2)) / dpr;
  };
  cardW = fit(cardW, canvasW);
  cardH = fit(cardH, canvasH);

  const radius = Math.min(cardW * 1.45, stageW * 0.66);
  const distance = cardW * 3;
  const sideZ = radius * (Math.cos(SPREAD) - 1);
  const stepPx = (radius * Math.sin(SPREAD) * distance) / (distance - sideZ);

  return { stageW, stageH, cardW, cardH, radius, distance, pad, stepPx, dpr, canvasW, canvasH };
}
