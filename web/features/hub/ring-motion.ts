/**
 * How the ring turns. Positions are in steps: card `i` is at the front when `x === i`
 * (modulo the number of cards), and `x` is unbounded so the ring can keep turning one way.
 *
 * The feel is a whip with a long tail. Far from its target the ring closes the distance
 * with a short time constant; inside the last third of a step the time constant grows
 * several times over, so the card glides into place for more than a second. There is no
 * spring and no overshoot, and a new target can arrive at any moment: the state is just
 * a position.
 */

export const CARD_COUNT = 4;

export interface RingMotion {
  /** Position in steps. */
  x: number;
  /** Where the ring is heading, a whole number of steps. */
  target: number;
  /** Steps per second, signed; drives the smear. */
  v: number;
}

/** Time constant a whole step or more from the target: the whip. */
const WHIP = 0.085;
/** Time constant close to the target: the tail. */
const TAIL = 0.6;
/** How early the whip hands over to the tail: higher lets go sooner. */
const HANDOVER = 1.7;
/** Fastest change of speed, steps per second squared: a few frames of wind-up. */
const MAX_ACCELERATION = 220;
/** Below this distance the ring is at rest. */
const REST = 0.0006;

function smoothstep(edge0: number, edge1: number, value: number): number {
  const t = Math.min(1, Math.max(0, (value - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Advance the ring by `dt` seconds toward its target. Returns true while it still moves. */
export function stepMotion(motion: RingMotion, dt: number): boolean {
  const error = motion.target - motion.x;
  const distance = Math.abs(error);
  if (distance < REST) {
    motion.x = motion.target;
    motion.v = 0;
    return false;
  }
  // The rate falls smoothly with the distance left, so the whip brakes without a jolt.
  const far = Math.min(1, distance) ** HANDOVER;
  const rate = 1 / TAIL + (1 / WHIP - 1 / TAIL) * far;
  let velocity = (error * (1 - Math.exp(-dt * rate))) / dt;
  // Speeding up takes a few frames; slowing down is the curve's own business.
  const limit = Math.abs(motion.v) + MAX_ACCELERATION * dt;
  if (Math.abs(velocity) > limit) velocity = Math.sign(velocity) * limit;
  motion.x += velocity * dt;
  motion.v = velocity;
  return true;
}

/** Where a released drag should settle: the card the flick is heading for. */
export function releaseTarget(x: number, velocity: number, from: number): number {
  const projected = Math.round(x + Math.max(-1.4, Math.min(1.4, velocity * 0.22)));
  // A clear flick always turns at least one card, even when the drag itself was short.
  if (projected === from && Math.abs(velocity) > 1.1) return from + Math.sign(velocity);
  return projected;
}

/** The target that brings card `index` to the front by the shortest way round. */
export function nearestTarget(x: number, index: number): number {
  const base = Math.round(x);
  let delta = (((index - base) % CARD_COUNT) + CARD_COUNT) % CARD_COUNT;
  if (delta > CARD_COUNT / 2) delta -= CARD_COUNT;
  return base + delta;
}

/** Index of the card at (or nearest to) the front. */
export function frontIndex(x: number): number {
  return ((Math.round(x) % CARD_COUNT) + CARD_COUNT) % CARD_COUNT;
}

/** Signed distance of card `index` from the front, in steps, wrapped to [-2, 2). */
export function offsetOf(index: number, x: number): number {
  const half = CARD_COUNT / 2;
  return ((((index - x + half) % CARD_COUNT) + CARD_COUNT) % CARD_COUNT) - half;
}

/** How strongly the ring smears, 0 to 1, from its speed in steps per second. */
export function smearOf(velocity: number): number {
  return smoothstep(0.9, 9, Math.abs(velocity));
}
