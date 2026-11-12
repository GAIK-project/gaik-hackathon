import {
  CanvasTexture,
  LinearFilter,
  LinearMipmapLinearFilter,
  Mesh,
  NoColorSpace,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";

import { HUB_CARDS } from "./cards";
import { FACING, SPREAD, type RingLayout } from "./ring-layout";
import { offsetOf } from "./ring-motion";

/**
 * The WebGL scene of the hub's ring: four card planes on a ring, and the glows the front
 * card casts onto the stage above and below it. One world unit is one CSS pixel.
 *
 * Three things happen in the shaders, because CSS cannot do them:
 * - the lens: past the front card the picture is stretched sideways, more the further out,
 *   so a card at the edge looks wider than it is and narrows as it comes forward;
 * - the smear: while the ring whips, each card is averaged along its own width, so the
 *   card in between flashes past as a streak instead of strobing;
 * - the glow: two soft pools of the front card's colour, blended between cards as the
 *   ring turns.
 *
 * It draws only when `render` is called; the controller decides when a frame is needed.
 */

/** How far a card's plane reaches past the card on each side, in card widths: room for the smear. */
const REACH = 0.9;
/** Corner radius of a card, CSS pixels (the card radius of the design system). */
const CORNER = 16;
/** Most taps of the smear. */
const TAPS = 16;

const cardVertex = /* glsl */ `
  uniform vec2 uHalf;
  uniform float uCardW;
  uniform float uLens;
  varying vec2 vUv;

  void main() {
    vUv = vec2(uv.x * ${(1 + 2 * REACH).toFixed(2)} - ${REACH.toFixed(2)}, uv.y);
    vec4 clip = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    // Screen position in CSS pixels from the middle of the stage.
    vec2 px = clip.xy / clip.w * uHalf;
    // The lens leaves the front card alone and stretches what lies beside it.
    float side = smoothstep(0.62, 2.4, abs(px.x) / uCardW);
    px.x *= 1.0 + uLens * side;
    px.y *= 1.0 + uLens * 0.3 * side;
    clip.xy = px / uHalf * clip.w;
    gl_Position = clip;
  }
`;

const cardFragment = /* glsl */ `
  precision highp float;
  uniform sampler2D uMap;
  uniform vec2 uSize;
  uniform float uPixel;
  uniform float uSmear;
  uniform int uTaps;
  uniform float uAlpha;
  uniform float uDim;
  uniform float uFlash;
  varying vec2 vUv;

  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
  }

  // Signed distance to the rounded card, in CSS pixels; negative inside.
  float card(vec2 uv) {
    vec2 q = abs((uv - 0.5) * uSize) - (uSize * 0.5 - ${CORNER.toFixed(1)});
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - ${CORNER.toFixed(1)};
  }

  void main() {
    // The plane is wider than the card to leave room for the streak; skip what it cannot reach.
    float reach = uSmear * 0.5 + 0.02;
    if (vUv.x < -reach || vUv.x > 1.0 + reach) discard;

    vec4 sum = vec4(0.0);
    float total = 0.0;
    float taps = float(uTaps);
    // Offsetting the taps per pixel turns banding in the streak into fine grain.
    float jitter = (hash(gl_FragCoord.xy) - 0.5) / taps;
    for (int i = 0; i < ${TAPS}; i++) {
      if (i >= uTaps) break;
      float t = (float(i) + 0.5) / taps - 0.5 + jitter * step(1.5, taps);
      vec2 uv = vec2(vUv.x + t * uSmear, vUv.y);
      float d = card(uv);
      float inside = 1.0 - smoothstep(-uPixel, uPixel, d);
      vec3 colour = texture2D(uMap, clamp(uv, 0.0, 1.0)).rgb;
      // A hairline just inside the edge keeps a dark card apart from the dark stage.
      float rim = 1.0 - smoothstep(0.0, 1.0, abs(d + 1.0));
      colour = mix(colour, vec3(1.0), rim * 0.16);
      // Taps near the middle count more, so the streak tapers off instead of ending in a step.
      float weight = 1.0 - 1.7 * abs(t);
      sum += vec4(colour * inside, inside) * weight;
      total += weight;
    }
    sum /= total;
    vec3 colour = sum.rgb * uDim * (1.0 + uFlash);
    gl_FragColor = vec4(colour, sum.a) * uAlpha;
  }
`;

const glowVertex = /* glsl */ `
  uniform vec2 uHalf;
  varying vec2 vPx;

  void main() {
    vPx = position.xy * uHalf;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

const glowFragment = /* glsl */ `
  precision highp float;
  uniform vec3 uColour;
  uniform vec2 uCard;
  uniform vec2 uHalf;
  uniform float uPower;
  varying vec2 vPx;

  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
  }

  void main() {
    // Above: a wide haze that rises from behind the top of the card.
    vec2 above = (vPx - vec2(0.0, uCard.y * 0.5)) / vec2(uCard.x * 1.75, uCard.y * 0.44);
    // Below: a flatter pool, like light landing on a floor.
    vec2 below = (vPx + vec2(0.0, uCard.y * 0.56)) / vec2(uCard.x * 1.5, uCard.y * 0.3);
    float light = 0.42 * exp(-dot(above, above) * 1.35) + 0.56 * exp(-dot(below, below) * 1.5);
    // Nothing reaches the top or bottom edge of the canvas, so no seam shows on the page.
    float edge = smoothstep(0.0, 0.2, 1.0 - abs(vPx.y) / uHalf.y);
    light *= edge * uPower;
    vec3 colour = uColour * light;
    colour += (hash(gl_FragCoord.xy) - 0.5) / 255.0;
    float alpha = clamp(max(colour.r, max(colour.g, colour.b)), 0.0, 1.0);
    gl_FragColor = vec4(max(colour, 0.0), alpha);
  }
`;

export interface RingFrame {
  /** Ring position in steps. */
  x: number;
  /** Smear, 0 to 1. */
  smear: number;
  /** Entrance, 0 (nothing) to 1 (all there). */
  intro: number;
  /** The front card is being pressed, 0 to 1. */
  press: number;
}

export interface RingScene {
  resize(layout: RingLayout): void;
  /** Use `source` as the face of card `index`. */
  setFace(index: number, source: HTMLCanvasElement): void;
  render(frame: RingFrame): void;
  dispose(): void;
}

function smoothstep(edge0: number, edge1: number, value: number): number {
  const t = Math.min(1, Math.max(0, (value - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Build the scene on `canvas`. Throws when WebGL is not available. */
export function createRingScene(canvas: HTMLCanvasElement, onContextLost: () => void): RingScene {
  // Ask on a spare canvas first: without WebGL 2 the renderer would log an error before it throws.
  const probe = document.createElement("canvas").getContext("webgl2");
  if (!probe) throw new Error("WebGL 2 is not available");
  probe.getExtension("WEBGL_lose_context")?.loseContext();

  const renderer = new WebGLRenderer({
    canvas,
    alpha: true,
    antialias: false,
    premultipliedAlpha: true,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(1);

  const scene = new Scene();
  const camera = new PerspectiveCamera(40, 1, 10, 5000);

  const half = new Vector2(1, 1);
  const cardSize = new Vector2(1, 1);
  let layout: RingLayout | null = null;

  const glow = new Mesh(
    new PlaneGeometry(2, 2),
    new ShaderMaterial({
      vertexShader: glowVertex,
      fragmentShader: glowFragment,
      uniforms: {
        uColour: { value: new Vector3(0, 0, 0) },
        uCard: { value: cardSize },
        uHalf: { value: half },
        uPower: { value: 0 },
      },
      transparent: true,
      premultipliedAlpha: true,
      depthTest: false,
      depthWrite: false,
    }),
  );
  glow.frustumCulled = false;
  glow.renderOrder = -100;
  scene.add(glow);

  const geometry = new PlaneGeometry(1, 1, 48, 4);
  const cards = HUB_CARDS.map(() => {
    const material = new ShaderMaterial({
      vertexShader: cardVertex,
      fragmentShader: cardFragment,
      uniforms: {
        uMap: { value: null as CanvasTexture | null },
        uHalf: { value: half },
        uCardW: { value: 1 },
        uLens: { value: 0.2 },
        uSize: { value: cardSize },
        uPixel: { value: 1 },
        uSmear: { value: 0 },
        uTaps: { value: 1 },
        uAlpha: { value: 0 },
        uDim: { value: 1 },
        uFlash: { value: 0 },
      },
      transparent: true,
      premultipliedAlpha: true,
      depthTest: false,
      depthWrite: false,
    });
    const mesh = new Mesh(geometry, material);
    mesh.frustumCulled = false;
    mesh.visible = false;
    scene.add(mesh);
    return { mesh, material };
  });

  const lost = (event: Event) => {
    event.preventDefault();
    onContextLost();
  };
  canvas.addEventListener("webglcontextlost", lost);

  return {
    resize(next) {
      layout = next;
      const cssW = next.canvasW / next.dpr;
      const cssH = next.canvasH / next.dpr;
      renderer.setSize(next.canvasW, next.canvasH, false);
      half.set(cssW / 2, cssH / 2);
      cardSize.set(next.cardW, next.cardH);
      camera.aspect = cssW / cssH;
      camera.fov = (2 * Math.atan(cssH / 2 / next.distance) * 180) / Math.PI;
      camera.near = next.distance * 0.05;
      camera.far = next.distance + next.radius * 4;
      camera.position.set(0, 0, next.distance);
      camera.updateProjectionMatrix();
      for (const { material } of cards) {
        material.uniforms.uCardW.value = next.cardW;
        material.uniforms.uPixel.value = 0.75 / next.dpr;
      }
    },

    setFace(index, source) {
      const { material, mesh } = cards[index];
      (material.uniforms.uMap.value as CanvasTexture | null)?.dispose();
      const texture = new CanvasTexture(source);
      texture.colorSpace = NoColorSpace;
      texture.generateMipmaps = true;
      texture.minFilter = LinearMipmapLinearFilter;
      texture.magFilter = LinearFilter;
      texture.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
      material.uniforms.uMap.value = texture;
      mesh.visible = true;
    },

    render({ x, smear, intro, press }) {
      if (!layout) return;
      const colour = (glow.material as ShaderMaterial).uniforms.uColour.value as Vector3;
      colour.set(0, 0, 0);
      let weight = 0;

      cards.forEach(({ mesh, material }, index) => {
        const offset = offsetOf(index, x);
        const away = Math.abs(offset);
        const angle = offset * SPREAD;
        const front = smoothstep(0, 1, 1 - away);
        const scale = 1 - 0.03 * press * front;

        mesh.position.set(
          layout!.radius * Math.sin(angle),
          0,
          layout!.radius * (Math.cos(angle) - 1),
        );
        mesh.rotation.y = angle * FACING;
        mesh.scale.set(layout!.cardW * (1 + 2 * REACH) * scale, layout!.cardH * scale, 1);
        // Far cards draw first; the back of the ring stays in the dark.
        mesh.renderOrder = -away;

        const uniforms = material.uniforms;
        uniforms.uAlpha.value = intro * (1 - smoothstep(1.2, 1.75, away));
        uniforms.uDim.value = 0.4 + 0.6 * front;
        uniforms.uSmear.value = smear * 0.5;
        uniforms.uTaps.value = smear > 0.002 ? Math.round(8 + (TAPS - 8) * smear) : 1;
        uniforms.uFlash.value = smear * 0.2;
        mesh.visible = uniforms.uMap.value !== null && uniforms.uAlpha.value > 0.001;

        const cast = HUB_CARDS[index].glow;
        colour.x += cast[0] * front;
        colour.y += cast[1] * front;
        colour.z += cast[2] * front;
        weight += front;
      });

      if (weight > 0) colour.multiplyScalar(1 / weight);
      (glow.material as ShaderMaterial).uniforms.uPower.value =
        intro * Math.min(1, 0.55 + weight * 0.45) * (1 + 0.35 * smear);

      renderer.render(scene, camera);
    },

    dispose() {
      canvas.removeEventListener("webglcontextlost", lost);
      for (const { material } of cards) {
        (material.uniforms.uMap.value as CanvasTexture | null)?.dispose();
        material.dispose();
      }
      geometry.dispose();
      glow.geometry.dispose();
      (glow.material as ShaderMaterial).dispose();
      renderer.dispose();
    },
  };
}
