import type { HubCard } from "./cards";

/**
 * Draws one card of the ring onto a 2D canvas: the art, a scrim, the mark, the demo's name
 * and one line. The scene uses the canvas as a texture, so the text bends, smears and
 * dims with the card. The 2 x 2 grid in `ring.tsx` shows the same card in plain HTML.
 */

/** One stroked shape of a mark, in the icon's own 24 x 24 grid. */
export type MarkShape = Path2D;

/** Read the shapes of a rendered line icon (lucide draws on a 24 x 24 grid). */
export function readMark(svg: SVGSVGElement): MarkShape[] {
  const shapes: MarkShape[] = [];
  const number = (element: Element, name: string) => Number(element.getAttribute(name) ?? 0);

  for (const element of Array.from(svg.children)) {
    const path = new Path2D();
    switch (element.tagName.toLowerCase()) {
      case "path":
        shapes.push(new Path2D(element.getAttribute("d") ?? ""));
        continue;
      case "circle":
        path.arc(number(element, "cx"), number(element, "cy"), number(element, "r"), 0, Math.PI * 2);
        break;
      case "ellipse":
        path.ellipse(
          number(element, "cx"),
          number(element, "cy"),
          number(element, "rx"),
          number(element, "ry"),
          0,
          0,
          Math.PI * 2,
        );
        break;
      case "rect":
        path.roundRect(
          number(element, "x"),
          number(element, "y"),
          number(element, "width"),
          number(element, "height"),
          number(element, "rx"),
        );
        break;
      case "line":
        path.moveTo(number(element, "x1"), number(element, "y1"));
        path.lineTo(number(element, "x2"), number(element, "y2"));
        break;
      case "polyline":
      case "polygon": {
        const values = (element.getAttribute("points") ?? "").trim().split(/[\s,]+/).map(Number);
        for (let index = 0; index + 1 < values.length; index += 2) {
          if (index === 0) path.moveTo(values[0], values[1]);
          else path.lineTo(values[index], values[index + 1]);
        }
        if (element.tagName.toLowerCase() === "polygon") path.closePath();
        break;
      }
      default:
        continue;
    }
    shapes.push(path);
  }
  return shapes;
}

export interface CardPaint {
  card: HubCard;
  art: CanvasImageSource & { width: number; height: number };
  mark: MarkShape[];
  /** Card size in CSS pixels. */
  width: number;
  height: number;
  dpr: number;
  /** The page's sans family, as CSS (`"Sora", "Sora Fallback"`). */
  fontFamily: string;
}

const INK = "#f2f4fa";
const SOFT_INK = "rgba(222, 227, 242, 0.8)";
/** The page background, for the scrim under the text. */
const STAGE = "8, 12, 26";

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Break `text` into lines no wider than `width`, at most `limit` of them. */
function wrap(context: CanvasRenderingContext2D, text: string, width: number, limit: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    const next = line ? `${line} ${word}` : word;
    if (line && context.measureText(next).width > width) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines.slice(0, limit);
}

export function paintCard(canvas: HTMLCanvasElement, paint: CardPaint): void {
  const { card, art, mark, width, height, dpr, fontFamily } = paint;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const context = canvas.getContext("2d");
  if (!context) return;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Art, cover-fit.
  const scale = Math.max(width / art.width, height / art.height);
  const artW = art.width * scale;
  const artH = art.height * scale;
  context.imageSmoothingQuality = "high";
  context.drawImage(art, (width - artW) / 2, (height - artH) / 2, artW, artH);

  // Scrim: the lower part of the card settles into the stage colour, so text reads on any art.
  const scrim = context.createLinearGradient(0, height * 0.34, 0, height);
  scrim.addColorStop(0, `rgba(${STAGE}, 0)`);
  scrim.addColorStop(0.42, `rgba(${STAGE}, 0.62)`);
  scrim.addColorStop(0.74, `rgba(${STAGE}, 0.9)`);
  scrim.addColorStop(1, `rgba(${STAGE}, 0.96)`);
  context.fillStyle = scrim;
  context.fillRect(0, 0, width, height);

  const pad = Math.round(width * 0.075);
  const inner = width - pad * 2;
  context.textBaseline = "alphabetic";

  // One line, at the bottom.
  const lineSize = clamp(width * 0.046, 13, 16);
  context.font = `400 ${lineSize}px ${fontFamily}`;
  const lines = wrap(context, card.line, inner, 2);
  const lineHeight = Math.round(lineSize * 1.45);
  let baseline = height - pad - (lines.length - 1) * lineHeight - Math.round(lineSize * 0.2);
  context.fillStyle = SOFT_INK;
  lines.forEach((text, index) => context.fillText(text, pad, baseline + index * lineHeight));

  // The demo's name: light and large, shrunk only if it would not fit on one line.
  let titleSize = clamp(width * 0.096, 21, 34);
  const titleFont = (size: number) => `${size >= 26 ? 300 : 400} ${size}px ${fontFamily}`;
  context.font = titleFont(titleSize);
  if ("letterSpacing" in context) context.letterSpacing = "-0.01em";
  while (titleSize > 17 && context.measureText(card.demo.title).width > inner) {
    titleSize -= 1;
    context.font = titleFont(titleSize);
  }
  baseline -= Math.round(lineSize * 1.05 + titleSize * 0.42);
  context.fillStyle = INK;
  context.fillText(card.demo.title, pad, baseline);
  if ("letterSpacing" in context) context.letterSpacing = "0px";

  // The mark: the demo's icon, large and thin.
  const markSize = clamp(width * 0.2, 40, 68);
  const top = baseline - titleSize * 0.95 - markSize - Math.round(width * 0.03);
  const unit = markSize / 24;
  context.save();
  context.translate(pad - unit * 1.5, top);
  context.scale(unit, unit);
  context.lineWidth = 1.25 / unit;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = INK;
  for (const shape of mark) context.stroke(shape);
  context.restore();
}
