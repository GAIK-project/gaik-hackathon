# web

The front end of gaik-hackathon: a Next.js static export that FastAPI serves at `/`. The
contract is `../docs/ARCHITECTURE.md` (sections 10, 13 and 14 for this folder).

## Commands

Bun is the package manager. From `web/`:

| Command | What |
| --- | --- |
| `bun install` | install |
| `bun run dev` | dev server on port 3000; calls the API on `http://localhost:8787` unless `NEXT_PUBLIC_API_BASE` says otherwise (`.env.example`) |
| `bunx tsc --noEmit` (or `bun run typecheck`) | type check |
| `bun run lint` | ESLint |
| `node node_modules/next/dist/bin/next build` | static build into `out/`, with Node as in the image |
| `bun run build` | the same build, then `scripts/flatten-export-segments.mjs` |

On Windows, Next.js 16.4.6 writes the segment prefetch files of an export under a wrong
name (`soita/__next.soita/__PAGE__.txt` instead of `soita/__next.soita.__PAGE__.txt`), so
every prefetch answers 404 until `node scripts/flatten-export-segments.mjs` has run. The
Linux build of the image is not affected and the script changes nothing there.

Static export rules: no dynamic route with run-time ids (use a query string,
`/soita?raportti=<id>`), no Server Actions, no rewrites, no `next/image` optimizer. A
component that reads `useSearchParams()` sits inside `<Suspense>`. Browser-only code (a
canvas, audio) is a client component loaded with `next/dynamic` and `ssr: false`.

## Layout

```
app/                  layout (fonts, dock, toaster), one page per route
components/shell/     Dock, PageShell, HowPanel, StackLogo, SourceChip, StatusWord, Pressable, Funding
components/ui/        shadcn/ui (style radix-nova, Radix primitives, lucide), themed by the tokens
features/<demo>/      everything specific to one demo: screen.tsx, how.ts and what the owner adds
lib/                  types.ts, api.ts, sse.ts, ws.ts, format.ts, stack.ts, demos.ts
public/brand/         GAIK logo (full white), GA mark, EU emblem (NEG): never cropped or recoloured
public/stack/         official stack logos; ids and files in lib/stack.ts
```

A demo page is `app/<route>/page.tsx`: PageShell (title, optional `lede`, `how`) around the
screen from `features/<demo>/screen.tsx`. The owner of a demo edits only `features/<demo>/`.

## Design tokens

Defined once in `app/globals.css` (single dark theme, `<html class="dark">`).

| Token | Tailwind utilities | Use |
| --- | --- | --- |
| `--bg` | `bg-bg`, `bg-background` | page |
| `--surface` | `bg-surface`, `bg-card` | cards, panels |
| `--surface-2` | `bg-surface-2`, `bg-muted`, `bg-secondary`, `bg-popover` | raised or hovered surface |
| `--line` | `border-line`, `border-border` | hairlines |
| `--fg` | `text-fg`, `text-foreground` | text |
| `--muted` | `text-muted-foreground` | secondary text |
| `--accent` | `bg-accent`, `text-accent`, `bg-primary`, `ring-ring` | the one accent |
| `--accent-fg` | `text-accent-fg`, `text-primary-foreground` | text on accent |
| `--brand-a`, `--brand-b` | `from-brand-a`, `to-brand-b`, `.stage-glow` | logo gradient: background glows only |
| `--ok`, `--warn`, `--danger` | `text-ok`, `text-warn`, `text-danger` (`bg-…` too) | state only |

Two names differ from shadcn's defaults, on purpose:

- `text-muted` is not secondary text. Secondary text is `text-muted-foreground`; `bg-muted`
  is a quiet surface, as shadcn components expect.
- `accent` is the design system's one accent (bright), not shadcn's hover surface. For a
  hover surface use `hover:bg-surface-2` or `hover:bg-muted`. A component added later with
  `shadcn add` that uses `bg-accent` for hover needs that class changed.

Radius: `rounded-lg` or `rounded-control` (10 px) for controls, `rounded-xl` or
`rounded-card` (16 px) for cards, `rounded-full` only for the dock. Shadows: every
`shadow-*` utility draws nothing; the focal element of a view may carry `shadow-glow`, and
`.stage-glow` puts the logo gradient behind it.

Type: Sora (`font-sans`, 300 to 700; display sizes are light) and JetBrains Mono
(`font-mono`) for component and tool names only.

Motion: transform and opacity only. `ease-stage` (in, feedback) and `ease-exit` (out);
`duration-150` to `200` for feedback, `300` for panels. `<Button>` and `<Pressable>` scale
on press; `.press` does the same for anything else. `animate-rise` and `animate-word-rise`
are the entrance keyframes; use one orchestrated entrance per page. Reduced motion shows
the end state (CSS in `globals.css`, `MotionConfig reducedMotion="user"` for Motion).

## Shell

- `useDock()` from `components/shell`: `setHidden(true)` hides the dock on the current page
  (a live call); `howOpen`, `setHowOpen`, `toggleHow` drive the "Näin tämä on tehty" panel.
- Page changes are React view transitions: `PageTransition` (inside PageShell) fades pages,
  and the dock's active pill is a shared element. A page that does not use PageShell wraps
  itself in `PageTransition` as its outermost element.
- `StackLogo id` renders a logo from `lib/stack.ts` as an `<img>`; an unknown id renders nothing.
- The bottom padding of a page is `pb-(--dock-clearance)`, so nothing ends under the dock.

## Data

- `lib/api.ts`: `api.get/post/patch/delete/postForm/blob`, `apiUrl(path)`; failures throw
  `ApiError` (`status`, `code`, Finnish `message`; `errorMessage(e)` for anything thrown).
- `lib/sse.ts`: `subscribe(url, { on: { report: … } })` for GET streams (reconnects with
  backoff, returns `{ close }`), `postStream(url, { json | body }, { on: { … } })` for POST
  streams (resolves after `done`, rejects with `ApiError`). Event maps are in `lib/types.ts`.
- `lib/ws.ts`: `wsUrl("/api/call/ws")`.
- `lib/format.ts`: Finnish dates, times, numbers, durations, sizes.
