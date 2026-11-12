import { HUB_CARDS } from "./cards";
import { ringLayout, type RingLayout } from "./ring-layout";
import {
  frontIndex,
  nearestTarget,
  releaseTarget,
  smearOf,
  stepMotion,
  type RingMotion,
} from "./ring-motion";
import type { RingScene } from "./ring-scene";
import { paintCard, type MarkShape } from "./ring-texture";

/**
 * Runs the ring: loads the scene and the art, turns pointer input into motion, and draws a
 * frame only while something moves. React (`ring.tsx`) owns the DOM and the links; this
 * class owns everything that changes per frame.
 */

export interface RingHost {
  stage: HTMLElement;
  canvas: HTMLCanvasElement;
  /** The shapes of each card's mark, in card order. */
  marks: MarkShape[][];
  /** The card at the front changed. */
  onFront(index: number): void;
  /** The scene could not start or lost its context: show the grid instead. */
  onFail(): void;
}

/** Pointer travel before a press becomes a drag, CSS pixels. */
const DRAG_START = 7;
/** The ring turns by itself until the visitor touches it. */
const AUTOPLAY_FIRST_MS = 4200;
const AUTOPLAY_EVERY_MS = 4600;
/** The entrance: the ring whips in from this many steps away while it fades in. */
const INTRO_FROM = -1.3;
const INTRO_MS = 520;

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Image failed to load: ${src}`));
    image.src = src;
  });
}

export class RingController {
  private readonly motion: RingMotion = { x: INTRO_FROM, target: 0, v: 0 };
  private scene: RingScene | null = null;
  private layout: RingLayout | null = null;
  private art: HTMLImageElement[] = [];
  private readonly faces = HUB_CARDS.map(() => document.createElement("canvas"));
  private fontFamily = "sans-serif";

  private disposed = false;
  private frame = 0;
  private lastTime = 0;
  private introStart = 0;
  private smear = 0;
  private press = 0;
  private pressTarget = 0;
  private front = -1;
  /** A test hook froze the ring at a given frame. */
  private frozen: { x: number; smear: number } | null = null;

  private pointer: number | null = null;
  private dragging = false;
  /** Clicks before this time belong to a drag that just ended. */
  private ignoreClicksUntil = 0;
  private startX = 0;
  private startY = 0;
  private startAt = 0;
  private lastMoveTime = 0;

  private interacted = false;
  private inView = true;
  private autoplay = 0;
  private resizeTimer = 0;
  private resizeObserver: ResizeObserver | null = null;
  private viewObserver: IntersectionObserver | null = null;

  constructor(private readonly host: RingHost) {}

  /** Load the scene and the art, then start. Fails (and calls `onFail`) without WebGL. */
  async start(): Promise<void> {
    try {
      const [{ createRingScene }, art] = await Promise.all([
        import("./ring-scene"),
        Promise.all(HUB_CARDS.map((card) => loadImage(card.art))),
      ]);
      // The card faces are drawn with the page's own font, so wait until it has loaded.
      const family = getComputedStyle(document.documentElement).getPropertyValue("--font-sora").trim();
      if (family) {
        this.fontFamily = family;
        await Promise.all(
          ["300 30px", "400 14px"].map((font) => document.fonts.load(`${font} ${family}`)),
        ).catch(() => undefined);
      }
      if (this.disposed) return;

      this.art = art;
      this.scene = createRingScene(this.host.canvas, () => this.fail());
      this.measure(true);
    } catch {
      this.fail();
      return;
    }

    const { stage } = this.host;
    stage.addEventListener("pointerdown", this.onPointerDown);
    stage.addEventListener("pointermove", this.onPointerMove);
    stage.addEventListener("pointerup", this.onPointerUp);
    stage.addEventListener("pointercancel", this.onPointerCancel);
    stage.addEventListener("click", this.onClick, true);
    document.addEventListener("visibilitychange", this.onVisibility);

    this.resizeObserver = new ResizeObserver(() => {
      window.clearTimeout(this.resizeTimer);
      this.resizeTimer = window.setTimeout(() => this.measure(false), 120);
    });
    this.resizeObserver.observe(stage);
    this.viewObserver = new IntersectionObserver(([entry]) => {
      this.inView = entry.isIntersecting;
    });
    this.viewObserver.observe(stage);

    this.introStart = performance.now();
    this.wake();
    this.autoplay = window.setTimeout(this.turnByItself, AUTOPLAY_FIRST_MS);
  }

  /** Turn by `direction` cards (1: the card on the right comes forward). */
  step(direction: number): void {
    this.touch();
    this.motion.target = Math.round(this.motion.target) + direction;
    this.wake();
  }

  /** Bring card `index` to the front by the shortest way. */
  goTo(index: number): void {
    this.touch();
    this.motion.target = nearestTarget(this.motion.x, index);
    this.wake();
  }

  /** Hold the ring at a given frame (for looking at a turn in a screenshot); null releases it. */
  freeze(frame: { x: number; smear: number } | null): void {
    this.touch();
    this.frozen = frame;
    if (frame) this.motion.x = this.motion.target = frame.x;
    else this.motion.target = Math.round(this.motion.x);
    this.wake();
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    window.clearTimeout(this.autoplay);
    window.clearTimeout(this.resizeTimer);
    this.resizeObserver?.disconnect();
    this.viewObserver?.disconnect();
    const { stage } = this.host;
    stage.removeEventListener("pointerdown", this.onPointerDown);
    stage.removeEventListener("pointermove", this.onPointerMove);
    stage.removeEventListener("pointerup", this.onPointerUp);
    stage.removeEventListener("pointercancel", this.onPointerCancel);
    stage.removeEventListener("click", this.onClick, true);
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.scene?.dispose();
    this.scene = null;
  }

  private fail(): void {
    if (this.disposed) return;
    this.dispose();
    this.host.onFail();
  }

  /** The visitor took over: the ring never turns by itself again. */
  private touch(): void {
    this.interacted = true;
    window.clearTimeout(this.autoplay);
  }

  private readonly turnByItself = (): void => {
    if (this.interacted || this.disposed) return;
    if (this.inView && !document.hidden) {
      this.motion.target = Math.round(this.motion.target) + 1;
      this.wake();
    }
    this.autoplay = window.setTimeout(this.turnByItself, AUTOPLAY_EVERY_MS);
  };

  /** Read the stage's size, place the canvas on whole device pixels, repaint the faces. */
  private measure(first: boolean): void {
    if (!this.scene) return;
    const { stage, canvas } = this.host;
    const rect = stage.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    const previous = this.layout;
    const layout = ringLayout(rect.width, rect.height, window.devicePixelRatio || 1);
    this.layout = layout;

    const cssH = layout.canvasH / layout.dpr;
    canvas.style.width = `${layout.canvasW / layout.dpr}px`;
    canvas.style.height = `${cssH}px`;
    // Centre the canvas on the stage, then snap it to device pixels so the front card's
    // text is not resampled.
    const top = (layout.stageH - cssH) / 2;
    const pageTop = rect.top + window.scrollY + top;
    const snapped = Math.round(pageTop * layout.dpr) / layout.dpr;
    canvas.style.top = `${top + (snapped - pageTop)}px`;

    stage.style.setProperty("--card-w", `${layout.cardW}px`);
    stage.style.setProperty("--card-h", `${layout.cardH}px`);
    this.scene.resize(layout);

    const repaint =
      first ||
      !previous ||
      previous.cardW !== layout.cardW ||
      previous.cardH !== layout.cardH ||
      previous.dpr !== layout.dpr;
    if (repaint) {
      HUB_CARDS.forEach((card, index) => {
        paintCard(this.faces[index], {
          card,
          art: this.art[index],
          mark: this.host.marks[index] ?? [],
          width: layout.cardW,
          height: layout.cardH,
          dpr: layout.dpr,
          fontFamily: this.fontFamily,
        });
        this.scene?.setFace(index, this.faces[index]);
      });
    }
    this.wake();
  }

  private wake(): void {
    if (this.frame || this.disposed) return;
    this.lastTime = 0;
    this.frame = requestAnimationFrame(this.tick);
  }

  private readonly tick = (now: number): void => {
    this.frame = 0;
    if (this.disposed || !this.scene) return;
    // A slow device takes longer steps instead of falling behind the clock: the motion is
    // exponential, so a long step is stable.
    const dt = this.lastTime ? Math.min(Math.max((now - this.lastTime) / 1000, 0.001), 1 / 10) : 1 / 60;
    this.lastTime = now;

    let moving = false;
    if (this.frozen) {
      this.motion.x = this.frozen.x;
      this.motion.v = 0;
    } else if (this.dragging) {
      // A finger that stopped moving stops smearing.
      if (now - this.lastMoveTime > 60) this.motion.v *= Math.exp(-dt * 14);
      moving = true;
    } else {
      moving = stepMotion(this.motion, dt);
    }

    // The smear comes up at once and lets go a little slower than the speed does.
    const smearTarget = this.frozen ? this.frozen.smear : smearOf(this.motion.v);
    const rate = smearTarget > this.smear ? 60 : 16;
    this.smear += (smearTarget - this.smear) * (1 - Math.exp(-dt * rate));
    if (Math.abs(smearTarget - this.smear) < 0.002) this.smear = smearTarget;

    this.press += (this.pressTarget - this.press) * (1 - Math.exp(-dt * 22));
    if (Math.abs(this.pressTarget - this.press) < 0.004) this.press = this.pressTarget;

    const introLinear = Math.min(1, (now - this.introStart) / INTRO_MS);
    const intro = 1 - (1 - introLinear) ** 3;

    const front = frontIndex(this.motion.x);
    if (front !== this.front) {
      this.front = front;
      this.host.onFront(front);
    }

    this.scene.render({ x: this.motion.x, smear: this.smear, intro, press: this.press });

    if (
      moving ||
      introLinear < 1 ||
      this.smear !== smearTarget ||
      this.press !== this.pressTarget
    ) {
      this.frame = requestAnimationFrame(this.tick);
    }
  };

  private readonly onPointerDown = (event: PointerEvent): void => {
    if (this.pointer !== null || (event.pointerType === "mouse" && event.button !== 0)) return;
    this.touch();
    this.pointer = event.pointerId;
    this.dragging = false;
    this.ignoreClicksUntil = 0;
    this.startX = event.clientX;
    this.startY = event.clientY;
    this.startAt = this.motion.x;
    // Pressing the front card answers with a slight scale, like every other control.
    this.pressTarget = (event.target as Element).closest("a") ? 1 : 0;
    this.wake();
  };

  private readonly onPointerMove = (event: PointerEvent): void => {
    if (event.pointerId !== this.pointer || !this.layout) return;
    const dx = event.clientX - this.startX;
    const dy = event.clientY - this.startY;
    if (!this.dragging) {
      if (Math.abs(dx) < DRAG_START || Math.abs(dx) < Math.abs(dy)) return;
      this.dragging = true;
      this.pressTarget = 0;
      this.frozen = null;
      this.motion.target = this.motion.x;
      this.lastMoveTime = event.timeStamp;
      this.host.stage.setPointerCapture(event.pointerId);
      this.host.stage.dataset.dragging = "true";
    }
    const next = this.startAt - dx / this.layout.stepPx;
    const elapsed = Math.max((event.timeStamp - this.lastMoveTime) / 1000, 0.004);
    const velocity = (next - this.motion.x) / elapsed;
    this.motion.v += (velocity - this.motion.v) * 0.5;
    this.motion.x = next;
    this.motion.target = next;
    this.lastMoveTime = event.timeStamp;
    this.wake();
  };

  private readonly onPointerUp = (event: PointerEvent): void => {
    if (event.pointerId !== this.pointer) return;
    this.pointer = null;
    this.pressTarget = 0;
    if (this.dragging) {
      this.endDrag(event.timeStamp - this.lastMoveTime < 90 ? this.motion.v : 0);
    } else if (this.layout && !(event.target as Element).closest("a")) {
      // A tap beside the front card brings that side's card forward.
      const rect = this.host.stage.getBoundingClientRect();
      const fromCentre = event.clientX - (rect.left + rect.width / 2);
      if (Math.abs(fromCentre) > this.layout.cardW / 2) {
        this.motion.target = Math.round(this.motion.target) + Math.sign(fromCentre);
      }
    }
    this.wake();
  };

  private readonly onPointerCancel = (event: PointerEvent): void => {
    if (event.pointerId !== this.pointer) return;
    this.pointer = null;
    this.pressTarget = 0;
    if (this.dragging) this.endDrag(0);
    this.wake();
  };

  private endDrag(velocity: number): void {
    this.dragging = false;
    this.ignoreClicksUntil = performance.now() + 350;
    delete this.host.stage.dataset.dragging;
    this.motion.target = releaseTarget(this.motion.x, velocity, Math.round(this.startAt));
  }

  /** A drag that ends over the front card must not open it. */
  private readonly onClick = (event: MouseEvent): void => {
    if (performance.now() > this.ignoreClicksUntil) return;
    this.ignoreClicksUntil = 0;
    event.preventDefault();
    event.stopPropagation();
  };

  private readonly onVisibility = (): void => {
    if (!document.hidden) this.wake();
  };
}
