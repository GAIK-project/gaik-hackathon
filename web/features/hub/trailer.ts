/**
 * The trailer of the hub. While this is null the hub renders nothing for it; when the film
 * exists, put the files under `public/` and set, for example:
 *
 *   export const TRAILER: Trailer | null = { src: "/trailer/gaik.mp4", poster: "/trailer/gaik.webp" };
 */
export interface Trailer {
  /** The film, a path under `public/`. */
  src: string;
  /** A still shown until the visitor presses play, 16:9. */
  poster: string;
}

export const TRAILER: Trailer | null = null;
