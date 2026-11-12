# gaik-hackathon: architecture and contract

This file is the contract every implementer works from. If code and this file disagree, fix the
code or raise it with the integrator; do not silently diverge. Sections marked **Owner** say who
may edit which files.

## 1. What we build

One web app with a hub and four demos, each built on components of the `gaik` library
(PyPI, version pinned in `server/pyproject.toml`). UI language: Finnish. Code, comments and docs:
English.

| Route | Demo | One line |
| --- | --- | --- |
| `/` | Hub | The four demos, how the whole thing is built, the trailer |
| `/soita` | Soita raportti | Talk to a voice agent (or send a voice note); a structured report lands on a live board and goes out by email after a person approves |
| `/studio` | GAIK Studio | Describe a use case; an app spec and a pipeline of gaik components appear and run live |
| `/ai-act` | AI Act -tarkastaja | Describe an AI system; get its risk tier, the obligations and the articles they rest on |
| `/tyokalut` | Työkalut agentille | The same components as MCP tools for ready-made agents, with a live activity log |

All sample content is synthetic. No customer material, no personal data.

## 2. Runtime

One container, one process:

- **FastAPI** (`server/app/main.py`) serves `/api/**`, the MCP endpoint `/mcp`, and the statically
  exported front end (`web/out`) at `/`.
- **Next.js static export** (`web/`, `output: "export"`): a client-side app; all data comes from
  `/api` on the same origin.
- **SQLite** at `$DATA_DIR/app.db`, files under `$DATA_DIR/files/`. One replica; in-process event
  bus for live updates.
- Outbound: Azure OpenAI (chat, realtime, transcription, embeddings), `gaik-decide` (Jev typed
  decisions), SMTP.
- **`bridge/`** is a separate local process (not in the container): the WhatsApp bridge. It holds
  the owner's personal Meta keys at run time only and forwards voice notes to the API.

## 3. Repository layout and owners

```
gaik-hackathon/
  docs/ARCHITECTURE.md        contract (integrator)
  assets/stack/*.svg          official stack logos, already downloaded (integrator)
  data/ai-act/                AI Act corpus (ai-act owner)
  scripts/                    one-off build scripts
  server/
    pyproject.toml            uv project; dependencies are declared by the foundation
    app/main.py               app factory, middleware, routers, static mount   [foundation]
    app/core/                 settings, auth, db, events, llm, files, models   [foundation]
    app/reports/              report types, intake pipeline, docx, email       [reports]
    app/voice/                realtime relay (WebSocket)                       [voice]
    app/studio/               catalog, spec generation, run, export, samples   [studio]
    app/aiact/                corpus index, assessment, report, eval           [aiact]
    app/mcp/                  MCP server, activity log                         [mcp]
    tests/<feature>/          tests per feature, same owner as the feature
  web/
    app/layout.tsx, app/page.tsx, app/<route>/page.tsx
    components/shell/         dock, page transition, how-it-is-made, logos     [web foundation]
    components/ui/            shadcn components                                [web foundation]
    lib/api.ts, lib/sse.ts, lib/types.ts                                       [web foundation]
    features/<demo>/          everything specific to one demo                  [that demo's owner]
    public/                   brand, stack logos, generated art
  bridge/                     WhatsApp bridge                                  [bridge]
  deploy/                     Dockerfile support, OpenShift manifests          [integrator]
```

Rules for parallel work:

- Edit only your own folders. A change you need in someone else's file goes into your final report
  as a request, with the exact diff you want.
- `app/core/models.py` and `web/lib/types.ts` hold the shared types. Feature owners may **add**
  types for their feature in their own module (`app/<feature>/models.py`,
  `web/features/<demo>/types.ts`) but do not change shared ones.
- Do not run `next build`, `next dev` or a second uvicorn on the shared ports; the integrator owns
  the running servers. Type-check with `bunx tsc --noEmit`, test Python with `uv run pytest`.
- A missing dependency: add it with one `uv add <pkg>` or `bun add <pkg>` (retry once if the lock
  is busy), never remove or upgrade someone else's, and name it in your report.

## 4. Conventions

- Python 3.12, uv, ruff (format + lint), pytest. FastAPI handlers are `async`; every blocking gaik
  or SDK call runs in a worker thread (`anyio.to_thread.run_sync`).
- Pydantic v2 models are the wire contract; field names are `snake_case` in JSON.
- Errors: HTTP status plus `{"error": {"code": "snake_case", "message": "Finnish sentence for the user"}}`.
  Provider error text never reaches the client; log it server-side.
- Times are ISO 8601 UTC with `Z`. Ids are opaque strings (`rep_3fa9c2…`).
- Server-sent events: `event: <name>\ndata: <json>\n\n`, a `: ping` comment every 15 s, and a final
  `event: done`. GET streams are consumed with `EventSource`, POST streams with
  `fetch` + `ReadableStream` (`web/lib/sse.ts`).
- User-facing text is Finnish, sentence case, short. No model names, token counts or timings in
  the interface, except inside the "Näin tämä on tehty" panel, whose job is to show them.
- No secret in the repo, in logs or in a response. Settings come from the environment (section 5).
- Every feature ships a test that runs without network (fakes for model calls) and one live smoke
  test marked `@pytest.mark.live` that makes a real, small call.

## 5. Settings

Model and service names (all present in the local `.env`): `AZURE_API_KEY`, `AZURE_ENDPOINT`,
`AZURE_API_VERSION`, `AZURE_DEPLOYMENT` (chat), `AZURE_TRANSCRIPTION_MODEL`, `EMBEDDING_MODEL`,
`AZURE_REALTIME_ENDPOINT`, `AZURE_REALTIME_API_KEY`, `AZURE_REALTIME_DEPLOYMENT`,
`AZURE_TTS_MODEL`, `TTS_ENDPOINT`, `GAIK_DECIDE_URL`, `GAIK_DECIDE_API_KEY`. `core/settings.py`
loads `<repo>/.env` if present (never overriding real environment variables) before anything
imports gaik. App-specific names:

| Name | Meaning |
| --- | --- |
| `HACK_BASIC_USER`, `HACK_BASIC_PASSWORD` | Browser login |
| `HACK_SESSION_SECRET` | Signs the session cookie |
| `HACK_API_KEY` | Bearer key for the bridge, the intake endpoints and `/mcp` |
| `HACK_AUTH_DISABLED` | `1` only in local development |
| `HACK_DEV` | `1` enables CORS for `http://localhost:3000` |
| `DATA_DIR` | Default `./.data` |
| `PUBLIC_BASE_URL` | Public origin, used in emails and MCP instructions |
| `SMTP_HOST`, `SMTP_PORT`, `MAIL_FROM` | Outbound mail; empty host means "mail disabled" |
| `MAIL_ALLOWED_TO` | Comma-separated allowlist; mail to any other address is refused |

## 6. Access control

- `GET /api/health` is open.
- `GET|POST /api/whatsapp/webhook` is open (Meta calls it; it stores, it does not act; section 9).
- `/mcp` and every `/api/**` route accept `Authorization: Bearer <HACK_API_KEY>` as an alternative
  to the session cookie (the bridge and agents use it).
- Everything else, static files included, needs the session cookie `gh_session`
  (HMAC-signed, 7 days, `HttpOnly`, `SameSite=Lax`, `Secure` outside dev). A request without it but
  with valid Basic credentials gets the cookie set on the response. Otherwise `401` with
  `WWW-Authenticate: Basic realm="GAIK mini-hackathon"`.
- WebSocket handshakes authenticate with the cookie (Safari does not send Basic credentials on a
  WebSocket); refuse with close code 4401.
- Compare secrets with `hmac.compare_digest`. No user accounts, no roles.

## 7. Shared types

TypeScript notation; `models.py` mirrors it one to one.

```ts
type Channel = "call" | "whatsapp" | "upload" | "text" | "mcp";
type ReportStatus = "live" | "processing" | "needs_approval" | "sent" | "failed";

interface FieldDef {
  name: string;            // snake_case key
  label: string;           // Finnish label
  type: "text" | "longtext" | "date" | "time" | "number" | "choice";
  required: boolean;
  description: string;     // what the value means; goes to the extractor and the voice agent
  options?: string[];      // for "choice"
}

interface ReportType {
  id: string;              // "turvallisuushavainto" | "tyomaapaivakirja" | "huoltoraportti" | user-made
  title: string;
  description: string;
  icon: string;            // lucide icon name
  requirement: string;     // the plain-language requirement the fields were generated from
  fields: FieldDef[];
  builtin: boolean;
}

interface FieldValue {
  name: string;
  label: string;
  value: string | null;
  evidence: string | null; // the words in the transcript the value rests on, when available
  source: "voice" | "extractor" | "edit";
}

interface TimelineEvent {
  id: string;
  at: string;
  kind: "call" | "transcribe" | "classify" | "extract" | "urgency" | "document" | "email" | "approve" | "edit" | "error";
  label: string;           // "Kentät poimittu 11/12"
  detail: string | null;   // "Puuttuu: sääolosuhteet"
  component: string | null;// gaik component or service behind the step, e.g. "extractor"
  status: "running" | "done" | "waiting" | "error";
  ms: number | null;
}

interface Urgency {
  label: "kiireellinen" | "normaali" | "ei kiireellinen";
  confidence: number;                    // 0..1
  probabilities: Record<string, number>;
  model: string;
}

interface ReportSummary {
  id: string;
  created_at: string;
  updated_at: string;
  channel: Channel;
  caller_label: string;    // "Selainpuhelu", "WhatsApp", never a phone number
  type_id: string;
  type_title: string;
  status: ReportStatus;
  filled: number;
  total: number;           // required fields
  urgency: Urgency | null;
  headline: string;        // one line built from the most telling field
}

interface Report extends ReportSummary {
  fields: FieldValue[];
  missing: string[];       // labels of required fields still empty
  transcript: { role: "agent" | "caller"; text: string; at: string }[];
  timeline: TimelineEvent[];
  files: { name: string; kind: "docx" | "audio"; size: number; url: string }[];
  email: { to: string | null; subject: string; body: string; status: "draft" | "sent" | "failed"; sent_at: string | null } | null;
}
```

## 8. HTTP API

### 8.1 Reports (owner: reports)

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /api/report-types` | | `ReportType[]` |
| `POST /api/report-types` | `{ "requirement": string }` | `ReportType` (fields made by gaik `schema_generator`) |
| `GET /api/reports` | `?limit=50` | `ReportSummary[]`, newest first |
| `GET /api/reports/stream` | | SSE: `report` with a `ReportSummary` on every create or change |
| `GET /api/reports/{id}` | | `Report` |
| `GET /api/reports/{id}/stream` | | SSE: `report` with the full `Report` on every change |
| `PATCH /api/reports/{id}/fields` | `{ "values": { [name]: string \| null } }` | `Report` (source `edit`, timeline event `edit`, document rebuilt) |
| `POST /api/reports/{id}/approve` | `{ "to"?: string }` | `Report` (mail sent to an allowlisted address; status `sent`) |
| `POST /api/reports/{id}/correct` | `{ "text": string }` or multipart `file` (audio) | `Report`: a correction given in words is applied by re-running the extractor on transcript + correction (timeline `edit`) |
| `GET /api/reports/{id}/files/{name}` | | the file |
| `GET /api/mail/recipients` | | `{ "recipients": string[], "enabled": boolean }` |
| `POST /api/intake/audio` | multipart: `file`, `type_id?`, `channel?` (default `upload`), `caller_label?` | `{ "report": Report, "reply": string }` after the pipeline finishes |
| `POST /api/intake/text` | `{ "text": string, "type_id"?: string, "channel"?: Channel, "caller_label"?: string }` | same |

The intake pipeline, each step one `TimelineEvent` (running, then done or error), each change
published on the event bus:

1. `transcribe`: audio to text with gaik's transcriber (skipped for text and for calls).
2. `classify`: when `type_id` is missing, pick the report type with Jev (`JevClassifier` through
   gaik-decide); below the confidence gate fall back to `turvallisuushavainto` and say so in the
   event detail.
3. `extract`: gaik `extractor` fills the type's fields from the transcript.
4. `urgency`: Jev classifies urgency with the three labels of `Urgency`.
5. `document`: a Word file from the fields.
6. `email`: a draft (subject, body, attachment), status `needs_approval`. Nothing is sent without
   `approve`.

A failing step records an `error` event and the pipeline continues with what it has; the report is
`failed` only when nothing useful exists. `reply` is a two-sentence Finnish summary for the sender
(used by the WhatsApp bridge).

`reports.service` exposes these to other features (voice, mcp): `create_report(...)`,
`update_fields(...)`, `add_event(...)`, `run_post_processing(report_id, transcript)`,
`list_reports()`, `get_report()`.

### 8.2 Voice call (owner: voice)

`WS /api/call/ws`

Client to server:

- text frame `{"type":"start","type_id":string,"language":"fi"|"en"|"sv"}` first;
- binary frames: microphone audio, PCM16 little-endian, mono, 24 000 Hz, 20 to 60 ms each;
- `{"type":"text","text":string}`: a typed caller turn (used by tests and by the "kirjoita" fallback);
- `{"type":"stop"}`.

Server to client:

- `{"type":"ready","report_id":string,"report_type":ReportType}`;
- binary frames: agent speech, same PCM16 24 kHz format;
- `{"type":"state","value":"listening"|"thinking"|"speaking"|"saving"|"done"}`;
- `{"type":"transcript","role":"agent"|"caller","text":string,"final":boolean,"item_id":string}`;
- `{"type":"fields","values":{[name]:string},"filled":number,"total":number,"asking":string|null}`;
- `{"type":"interrupt"}`: the caller spoke over the agent; stop playback at once;
- `{"type":"saved","report_id":string}` then the server closes normally;
- `{"type":"error","message":string}`.

Behaviour: the agent greets, lets the caller tell it in their own words, records values with a
`set_fields` tool as it hears them (that drives the live field list), asks for one missing required
field at a time, reads back a one-sentence summary, and calls `save_report` when the caller agrees.
The report exists from `start` with status `live`; every `fields` message also updates the report
so the board shows "Käynnissä, 3/5 kenttää". After `save_report` the server runs the pipeline
steps 3 to 6 on the call transcript (the extractor pass is grounded in the transcript and wins
over the tool arguments where they differ).

### 8.3 Studio (owner: studio)

```ts
type StudioTemplate = "documents_to_table" | "audio_to_report" | "documents_to_answers" | "documents_to_labels";

interface StudioStep { id: string; component: string; title: string; detail: string; gaik_import: string }

interface StudioSpec {
  id: string;
  title: string;                 // "Laskut taulukoksi"
  summary: string;               // one sentence
  requirement: string;           // the user's words
  template: StudioTemplate;
  input: { kind: "documents" | "audio"; accept: string[]; multiple: boolean; hint: string; sample_ids: string[] };
  steps: StudioStep[];           // the pipeline, in order
  fields: FieldDef[];            // for table and report templates
  classes: { name: string; description: string }[];  // for the labels template
  question_examples: string[];   // for the answers template
  output: { kind: "table" | "report" | "answer" | "labels" };
}
```

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /api/studio/catalog` | | `{ components: { id, title, purpose, gaik_import, extra }[], templates: { id, title, steps: string[] }[] }` |
| `GET /api/studio/samples` | | `{ id, title, kind, files: { name, url, size }[] }[]` |
| `POST /api/studio/generate` | `{ "prompt": string }` | SSE: `status {text}`, `step {step}` one at a time, `fields {fields}` or `classes {classes}`, `spec {spec}`, `done` |
| `POST /api/studio/run` | multipart: `spec` (JSON), `files[]` or `sample_id`, `question?` | SSE: `step {id, status: "running"\|"done"\|"error", ms, note}`, `result {...}`, `done` |
| `POST /api/studio/export` | `{ "spec": StudioSpec }` | `{ "python": string, "blueprint": object }` |

The model never writes free code. It chooses one template and fills the spec (structured output,
validated server-side; an invalid spec is repaired once, then refused). `run` executes the
template with real gaik components. Result shapes:

```ts
type StudioResult =
  | { kind: "table"; columns: { name: string; label: string }[]; rows: { source: string; values: Record<string, string | null> }[] }
  | { kind: "report"; fields: FieldValue[]; transcript: string; file: { name: string; url: string } | null }
  | { kind: "answer"; question: string; answer: string; sources: { title: string; page: number | null; quote: string }[]; found: boolean }
  | { kind: "labels"; rows: { source: string; label: string; confidence: number; probabilities: Record<string, number> }[] };
```

`export` is deterministic: a runnable Python script that calls the same gaik components, built
from a template per `StudioTemplate`, plus the blueprint JSON.

### 8.4 AI Act (owner: aiact)

```ts
type Tier = "prohibited" | "high" | "transparency" | "minimal";

interface Citation {
  unit_id: string;        // "annex-III-4-a", "art-50"
  label: string;          // the regulation's own citation style: "Liite III, 4 kohdan a alakohta"
  quote: string;          // verbatim words from the regulation
  verified: boolean;      // the quote was found in the unit's text
}

interface Obligation { title: string; summary: string; who: "provider" | "deployer" | "both"; citation: Citation }

interface Assessment {
  id: string;
  description: string;
  tier: Tier;
  probabilities: Record<Tier, number>;
  confidence: number;
  needs_human_review: boolean;   // low confidence or the two methods disagree
  reasoning: string;             // two or three Finnish sentences
  basis: Citation[];
  obligations: Obligation[];
  language: "fi";
  disclaimer: string;
}
```

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /api/ai-act/examples` | | `{ id, title, description }[]` |
| `POST /api/ai-act/assess` | `{ "description": string }` | SSE: `step {id, label, status, ms, component}`, `partial {probabilities}`, `result {assessment}`, `done` |
| `GET /api/ai-act/units/{unit_id}` | | `{ id, kind, label, title, text, paragraphs }` |
| `GET /api/ai-act/search` | `?q=` | `{ hits: { unit_id, label, snippet, score }[] }` |
| `POST /api/ai-act/report` | `{ "assessment": Assessment }` | docx file |
| `GET /api/ai-act/eval` | | `{ ran_at, total, correct, cases: { id, title, expected: Tier, got: Tier, ok: boolean }[] }` |

Method (the corpus is the consolidated text in force, see section 13): retrieve candidate provisions from the corpus; classify the tier with Jev (four classes
with descriptions) for calibrated probabilities; in parallel let a chat model pick the matching
provision and quote it; check every quote against the unit text; map the tier to obligations from
a reviewed table whose summaries were extracted from the articles; flag for human review when the
two methods disagree or confidence is low. The checker never says "compliant"; it says which
provisions apply and why, and that this is not legal advice.

### 8.5 MCP (owner: mcp)

`/mcp` serves MCP over Streamable HTTP, bearer-protected. Tools (names are the contract):

| Tool | Arguments | Returns |
| --- | --- | --- |
| `parse_document` | `filename`, `content_base64` | text of the document |
| `extract_fields` | `text`, `requirement` or `fields` | values with the generated schema |
| `transcribe` | `filename`, `content_base64`, `language?` | text |
| `classify` | `text`, `labels` (name to description) | label, confidence, probabilities |
| `search_ai_act` | `query` | passages with unit ids |
| `assess_ai_system` | `description` | tier, basis, obligations |
| `list_reports` | `status?` | report summaries |
| `get_report` | `report_id` | the report with fields and the file link |
| `create_report` | `text`, `type_id?` | the new report |

| Method and path | Response |
| --- | --- |
| `GET /api/mcp/info` | `{ url, tools: { name, description, arguments }[], clients: { id, title, snippet }[] }` (no key in it) |
| `GET /api/mcp/activity` | recent `{ id, at, tool, client, ok, ms, summary }[]` |
| `GET /api/mcp/activity/stream` | SSE: `call` per tool call, started and finished |
| `POST /api/mcp/try` | `{ "tool": string, "arguments": object }` runs one tool server-side for the page's "Kokeile" button |

## 9. WhatsApp bridge (owner: bridge)

No tunnel and no Meta secret on Rahti. The API only stores what Meta sends; the bridge on the
owner's machine does everything that needs a key.

API side (`server/app/whatsapp/`, owner: bridge):

| Method and path | Access | Behaviour |
| --- | --- | --- |
| `GET /api/whatsapp/webhook` | open | Meta's handshake: answer `hub.challenge` as plain text when `hub.verify_token` equals the token of the active bridge session, else 403 |
| `POST /api/whatsapp/webhook` | open | store the raw body and the `X-Hub-Signature-256` header in a bounded queue (200 items, 256 KB each, oldest dropped) and answer 200 at once; no parsing |
| `POST /api/whatsapp/session` | bearer | `{ "verify_token": string }` starts a bridge session (one at a time, expires after 12 h without polling); returns `{ "callback_url": string }` |
| `GET /api/whatsapp/pending?wait=25` | bearer | long poll: returns `{ "items": [{ "id", "received_at", "signature", "body" }] }` and removes them |
| `DELETE /api/whatsapp/session` | bearer | ends the session and clears the queue |
| `GET /api/whatsapp/status` | cookie or bearer | `{ "active": boolean, "last_poll_at": string \| null, "queued": number }` for the Soita page |

Bridge side (`bridge/`, Python, run with `bridge/run.ps1`): reads the Meta keys and the allowed
numbers from the owner's personal env library into its own process (named keys only, retry, reject
values that start with `encrypted:`), starts a session, subscribes the Meta app's webhook to
`PUBLIC_BASE_URL/api/whatsapp/webhook`, long-polls `pending`, verifies each item's signature with
the app secret, drops senders that are not allowed, downloads voice-note media, and calls the API
with the bearer key:

- voice note or text: `POST /api/intake/audio|text` with `channel=whatsapp`,
  `caller_label="WhatsApp"`; answer in WhatsApp with `reply`, the Word file as a document and two
  buttons, "Hyväksy ja lähetä" and "Korjaa";
- "Hyväksy ja lähetä": `POST /api/reports/{id}/approve` with no `to` (the server's default
  recipient; an address typed in WhatsApp is never passed on);
- "Korjaa": the bridge asks for the correction; the next message goes to
  `POST /api/reports/{id}/correct`.

It answers only inside the 24-hour window, de-duplicates by message id, removes the webhook
subscription and the session on exit (also `run.ps1 -Stop`). The phone number never reaches the
API. Tests simulate Meta with a signed payload and a fake Graph server.

## 10. Front end

- Next.js (App Router, static export), React, TypeScript, Tailwind CSS, shadcn/ui, Motion,
  three.js through React Three Fiber, lucide icons, bun. Versions are pinned by the web foundation.
- Shell: no top bar. One bottom dock (pill) on every page: the GAIK mark (home), the four demos
  as icon + short label, and "Näin tämä on tehty". The active item's background glides between
  items. Page changes use a view transition. The dock stays the same width on every page.
- Every demo page has the same two-part rhythm: the working surface first, and a
  "Näin tämä on tehty" panel that shows the pipeline as an animated strip (each step: what
  happens, the gaik component, the service behind it with its real logo). The data for the strip
  lives in `web/features/<demo>/how.ts`:

```ts
interface HowStep { id: string; title: string; detail: string; gaik?: string; logo?: string }
interface How { title: string; steps: HowStep[]; stack: string[] }   // stack: logo ids from public/stack
```

- Phone first (people arrive from a QR code): everything works at 390 px; nothing depends on hover.
- Motion: transform and opacity only; 150 to 200 ms feedback, 250 to 400 ms panels; ease-out in,
  shorter ease-in out; no bounce; one orchestrated entrance per page; every control answers a
  press with a slight scale; a view that updates live changes rows in place (no replayed
  entrances). With reduced motion show the end state.
- Real logos only through the downloaded files in `public/stack` and `public/brand`; never draw
  one. No decorative badges, no eyebrow labels, no numbered markers on things that are not a
  sequence, sentence case, short specific copy.

Section 14 holds the design tokens and shared components.

## 11. Verification

- Feature owner: unit tests with fakes, one live smoke test, `ruff check`, `ruff format --check`,
  `bunx tsc --noEmit`, `bun run lint`. Report the commands and their output.
- Integrator: full test run, static build, browser check of every page at 390 and 1440 px with
  real data, the live flows (call by typed turns and by microphone, audio upload, Studio run on
  samples, AI Act on the examples, MCP from a real client), then the same on Rahti.

## 12. Deployment

One image built on Rahti from this folder (binary Docker build from a `git archive` of HEAD),
one Deployment (1 replica, strategy Recreate), Service and edge Route in namespace `gaik`,
host `gaik-hackathon.2.rahtiapp.fi`. Every object carries
`app.kubernetes.io/part-of: mini-hackathon` so the demos form their own group in gaik-ops.
A 1 Gi volume (`standard-csi`) holds `DATA_DIR`. Route annotations:
`haproxy.router.openshift.io/timeout: 15m` (HTTP and SSE) and
`haproxy.router.openshift.io/timeout-tunnel: 1h` (WebSocket). The BuildConfig needs resources
(requests 500m / 2Gi, limits 2 CPU / 6Gi). In the image, bun installs and **Node** runs
`next build` (bun's build crashes on Rahti build nodes). Base `ubi9/python-312`, plus ffmpeg.
Secrets: the app's own secret `gaik-hackathon` (the `HACK_*` values and the gaik-decide key);
Azure chat credentials by `secretKeyRef` from `gaik-demo-api-keys`; Realtime from
`voice-agent-secrets`. Full commands and manifests: `.work/research/deploy.md`.

## 13. Facts from the research maps

Each implementer reads the map of their area in full (`.work/research/<name>.md`); these are the
facts that shaped the contract.

**gaik 0.8.14** (`gaik-api-core.md`, `gaik-api-rag-report.md`, `component-catalog.md`)

- Every gaik call is synchronous: wrap it in a worker thread. gaik prints to stdout and calls
  `load_dotenv()` at import from its own location, so the app loads its `.env` **before**
  importing gaik and never relies on gaik finding it.
- One config dict drives everything: `get_llm_config("azure", model=..., timeout=...)`. It reads
  `AZURE_API_KEY`, `AZURE_ENDPOINT`, `AZURE_API_VERSION`, `AZURE_DEPLOYMENT`,
  `AZURE_TRANSCRIPTION_MODEL`, `EMBEDDING_MODEL`. Structured calls time out at 30 s unless
  `config["timeout"]` is raised: use 120. Sampling parameters follow the deployment **name**; for a
  reasoning deployment pass `temperature=None` and `reasoning_effort`.
- Install without torch:
  `gaik[audio-to-structured-data,documents-to-structured-data-cpu,vision-extract,classifier,jev-classifier,source-tracing]==0.8.14`
  plus ffmpeg. Do not use `all-cpu`, `parser`, `retriever` or `rag-workflow` (Docling, torch).
- Fixed report schemas are built without a model call: `ExtractionRequirements` + `FieldSpec` +
  `create_extraction_model`. `SchemaGenerator` (two or more calls) is for "new report type" and
  the Studio.
- `DataExtractor.extract(extraction_model, requirements, user_requirements, documents=[text])`
  returns one dict per text, with no evidence. `SourceTracing().augment(...)` then
  `check_text(...)` adds a verbatim quote per value and checks it in code
  (`verified | check | reworded | unchecked`): that is `FieldValue.evidence`.
- `Transcriber(config, output_dir=<temp dir>).transcribe(path)` for voice notes (needs ffmpeg and
  a writable directory). `JevClassifier(api_key, base_url)` takes text and returns calibrated
  probabilities; it never reads the environment.
- RAG without a database: `Embedder(config, model=<deployment>)` and `VectorStore(persist=False)`.
  The in-memory search is a pure-Python scan (about 1 s per 1 000 chunks); prebuilt vectors with
  numpy are fine when faster search is needed, as long as the embeddings come from `Embedder`.
- gaik has no fields-to-Word component: reports are written with `python-docx`. `ReportWriter`
  (several model calls per section, needs Pandoc) is not used for single-call reports.
- `mcp` on stdio would be corrupted by gaik's prints; the MCP server is HTTP only.

**Realtime voice** (`voice-agent.md`, `web-research.md`)

- GA endpoint: `wss://<host>/openai/v1/realtime?model=<deployment>` with header `api-key`, no
  api-version. `session.update` uses the GA shape (`type: "realtime"`, audio nested under
  `audio.input` / `audio.output`, events `response.output_audio.delta`,
  `response.output_audio_transcript.delta`).
- The Realtime host differs from the chat host. Settings: `AZURE_REALTIME_ENDPOINT`,
  `AZURE_REALTIME_API_KEY`, `AZURE_REALTIME_DEPLOYMENT`.
- Session defaults that tested best in Finnish: voice `cedar`, language fixed in code, a scripted
  greeting that says it is an AI, input noise reduction, transcription with `gpt-4o-transcribe`
  when the deployment exists (fallback `whisper-1`), server VAD about 700 ms.
- The existing relay (`openai-voice-agent-with-structured-outputs/server/relay.py`,
  `lib/realtime_core.py`) and its browser audio (24 kHz `AudioContext`, mic and playback worklets
  with a `flush` for barge-in) are GAIK code and are ported, adapted to section 8.2.
- The call goes through our WebSocket relay, not browser WebRTC: it works on restrictive networks
  and the server sees every tool call and transcript.
- Verification without a microphone: typed turns through the relay, and a PCM file streamed at
  real-time pace into the local WebSocket.

**Rahti, env, mail** (`deploy.md`, `env.md`)

- Local: the repo `.env` (gitignored) is a copy of the library file
  `haaga-helia/gaik/gaik-hackathon.env`; it already exists with every name of section 5.
  Start the API with `uv run python -m uvicorn app.main:app --port 8787` from `server/`.
- gaik-decide: `GAIK_DECIDE_URL` (in the cluster `http://gaik-decide:8080`),
  `GAIK_DECIDE_API_KEY`; header `x-api-key`. Text sent to Jev leaves the EU: synthetic text only.
- Mail: `smtp.pouta.csc.fi:25`, no authentication, STARTTLS, only from Rahti pods. `From` and
  `Sender` are `MAIL_FROM` (a real haaga-helia.fi mailbox). It cannot be tested locally: with
  `SMTP_HOST` empty, "send" writes the message to `$DATA_DIR/outbox/` and reports `sent` with a
  note in the timeline that mail is disabled.
- MCP SDK is `mcp` 2.x: `from mcp.server import MCPServer`,
  `streamable_http_app(stateless_http=True, json_response=True, transport_security=...)` with the
  public hostname in `allowed_hosts`, the session manager run from the FastAPI lifespan, a bearer
  gate in front. A working snippet is in `web-research.md` section D.
- Next.js 16.4 static export: remove `cacheComponents` and `partialPrefetching`, set
  `output: "export"`, `trailingSlash: true`, `images.unoptimized`. No dynamic route with run-time
  ids: use query strings (`/soita?raportti=<id>`). FastAPI serves `web/out` with
  `StaticFiles(html=True)` mounted last. In development the web app runs on port 3000 and calls
  the API on 8787 (`NEXT_PUBLIC_API_BASE`, CORS on with `HACK_DEV=1`).
- Versions: next 16.4.6, react 19.3.x, tailwindcss 4.3.x, motion 14.x (`motion/react`),
  three 0.187, @react-three/fiber 9.8 (caps React below 19.4), TypeScript 5.9.

**AI Act corpus** (`ai-act-corpus.md`)

- `data/ai-act/corpus.fi.json` and `corpus.en.json` hold the regulation as published on
  12.7.2024 (180 recitals, 113 articles, 13 annexes, Annex III split into points). Regulation
  (EU) 2026/1744 amended it on 27.7.2026: new application dates (Annex III high-risk from
  2.12.2027, Annex I from 2.8.2028), two more prohibited practices from 2.12.2026, a new
  Article 4. Annex III, Article 5(1)(a) to (h), Article 6 and Article 50 are unchanged.
- The checker must cite the law in force: the aiact owner adds a parser for the consolidated
  text (CELEX `02024R1689-20260727`, fetched the same way) and uses it for articles and annexes,
  keeps recitals from the original, and shows the version it cites. Quote checks normalise
  whitespace, quotes and dashes on both sides.
- `data/ai-act/eval-cases.fi.json`: 36 cases with expected tiers; check its provision ids against
  the corpus before scoring.

**WhatsApp** (`whatsapp-connector.md`)

- Graph API v25.0. Voice notes arrive as `messages` of type `audio` (`audio/ogg; codecs=opus`);
  media is two GETs with the bearer token. Buttons: interactive `button` with up to three replies.
  Document: upload to `/{phone-number-id}/media`, then a `document` message.
- The Meta test app answers only code-verified recipients, only inside 24 hours of their last
  message, and has no template fallback.
- On 12.11.2026 the stored WhatsApp account and phone-number ids returned HTTP 400 (code 100,
  subcode 33) although the token is valid: the Meta app needs a look before a live run.
- WhatsApp calls are not built: Meta allows them on a test number, but the media is WebRTC that
  must end in our own server with outbound UDP, which Rahti does not document.

## 14. Design system

The rules of the product owner win over everything here: few well-made elements, no decorative
badges or eyebrow labels, no numbered markers on things that are not a sequence, sentence case,
real logos only, one accent, motion that answers an action.

**Direction.** A tinted dark stage, made for a projector and a trailer: deep blue-black, soft
light from the GAIK gradient behind the one thing that matters on each page, everything else
flat and quiet. Single theme (dark); no light mode.

**Tokens** (CSS variables in `web/app/globals.css`, mapped to Tailwind and shadcn):

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `oklch(0.155 0.028 265)` | page |
| `--surface` | `oklch(0.2 0.032 265)` | cards, panels |
| `--surface-2` | `oklch(0.245 0.036 265)` | raised or hovered surface |
| `--line` | `oklch(0.31 0.034 265)` | hairlines |
| `--fg` | `oklch(0.965 0.008 265)` | text |
| `--muted` | `oklch(0.72 0.03 265)` | secondary text |
| `--accent` | `oklch(0.74 0.14 242)` | the one accent: GAIK blue lifted for dark |
| `--accent-fg` | `oklch(0.16 0.03 265)` | text on accent |
| `--brand-a`, `--brand-b` | `#0079c2`, `#8a2387` | the gradient of the logo; only in the logo and in background glows |
| `--ok`, `--warn`, `--danger` | `oklch(0.78 0.15 160)`, `oklch(0.83 0.14 85)`, `oklch(0.7 0.19 25)` | state only, never decoration |

Radius: 16 px cards, 10 px controls, full only for the dock. One elevation: the focal element of
a view may carry a soft glow; nothing else has a shadow.

**Type.** Sora (300 to 700) for everything, JetBrains Mono for component and tool names, through
`next/font/google`. Display sizes are light (300) and large; body 15 to 16 px; labels 12 to 13 px.

**Brand assets** (copy into `web/public/brand/`): `gaik_logo_fullwhite.svg` from
`~/dev/hh/gaik/logos/gaik_logo_first_package/SVG/` (the colour logo is unreadable on dark), the
GA mark PNG from `public/logos/gaik-logo-letter-only.png` of the toolkit demo, and the
"Co-funded by the European Union" emblem, NEG variant, from
`public/co-funded_EN/horizontal/RGB/PNG/` of the toolkit demo. The EU emblem is mandatory: it
sits in the hub footer and in the "Näin tämä on tehty" panel, never smaller than the GAIK logo
beside it. Do not crop, recolour or redraw any logo. There is no verified Haaga-Helia logo: name
Haaga-Helia in text.

**Stack logos.** `assets/stack/*.svg` (svgl and Iconify, fetched 12.11.2026) are copied to
`web/public/stack/`; `*.dark.svg` is the variant for dark surfaces and is the one to use. The CSC
mark comes from `components/ui/svgs/csc.tsx` of the toolkit demo. Render through one
`<StackLogo id>` component as `<img>` (no inlining, so gradient ids cannot collide).

**Shared components** (web foundation, `web/components/shell/`):

- `Dock`: bottom pill on every page; GAIK mark (home), four demos (icon + short label), a
  "Näin tämä on tehty" toggle. The active background glides between items; the dock keeps one
  width. Safe-area aware; hides while a call is live.
- `PageShell`: page title (one line), optional one-line lede, the working surface, then `HowPanel`.
- `HowPanel`: the pipeline as an animated strip. Steps appear left to right once when the panel
  opens; each step shows its title, one line of detail, the gaik component in mono and the
  service logo. On a phone the strip is vertical.
- `StackLogo`, `SourceChip` (a quiet chip that opens the source on press), `StatusWord` (one
  word that swaps with a short rise), `Pressable` (slight scale on press), `Orb`
  (the voice focal point: a WebGL sphere driven by real audio levels, with a CSS fallback).
- `lib/api.ts` (typed fetch with the error shape of section 4), `lib/sse.ts` (`subscribe` for GET
  streams with reconnect, `postStream` for POST streams), `lib/types.ts` (section 7 and 8 types).

**Motion.** Transform and opacity only. 150 to 200 ms for feedback, 250 to 400 ms for panels and
view changes, ease-out `cubic-bezier(0.16, 1, 0.3, 1)` in and a shorter ease-in out, no bounce.
One orchestrated entrance per page (the hero of the hub; a per-word rise is welcome there). Page
changes use the React `<ViewTransition>`; the active pill of the dock is a shared element. Live
lists update rows in place. Every control answers a press. With reduced motion, show the end
state.

**Hub.** Brand first, no call to action in the hero. The four demos are the hero: a ring of four
cards in perspective that can be spun by drag, arrow keys or tap, each with its own colour world
and generated art, the front card casting its colour onto the stage (the reference for this feel
is a "warp carousel": fast whip, long slow tail; write our own, copy no code). Below it: how the
whole app is built (one diagram with real logos), the trailer when it exists, and the footer with
the GAIK logo and the EU emblem. On a phone the ring still works by swipe; with reduced motion it
is a plain 2 x 2 grid.

## 15. Amendments after review (these override anything above that disagrees)

Two Opus 5.5 reviewers and the two foundation builds produced these rules. Where an earlier
section says something else, this section wins.

### 15.1 What the foundation already provides (use it, do not rebuild it)

- Python `>=3.12,<3.13`. The gaik install line also carries the extra `embedder`.
- A feature exposes `router` in `app/<feature>/router.py` with **full paths**
  (`/api/reports`, `/api/call/ws`); `main.py` includes it without a prefix when the module
  imports. `server/app/whatsapp/` is a feature folder too (owner: bridge).
- `core/auth.py` is pure ASGI middleware over HTTP and WebSocket. A refused WebSocket is accepted
  and then closed with 4401.
- `core/db.get_store()`: thread-safe JSON document store (put, get, update, list, delete, count).
  Use `update(...)` for read-modify-write.
- `core/events.get_bus()`: `publish` is safe from any thread; `sse_response` / `sse_stream` add
  the ping, the final `done` and the no-buffering headers. Every SSE endpoint uses them. No GZip
  middleware anywhere. Subscribe before reading current state.
- `core/llm`: `gaik_config`, `run_blocking`, `jev_classifier`, `embedder`, `structured_chat`.
- `core/lifecycle.register(...)` for startup and shutdown work and
  `lifecycle.mount_asgi("/mcp", app)` for the MCP app (it sits inside the access middleware, so
  the MCP owner writes no gate of their own).
- `core/files`: save, open, list, `file_response`. `GET /api/files/{scope}/{name}` serves stored
  files behind the access rules.
- Tests inherit `server/tests/conftest.py` (own `DATA_DIR` per test, auth on, mail off).
- Web: `web/app/<route>/page.tsx` renders `web/features/<demo>/screen.tsx`; a demo owner edits
  only `web/features/<demo>/`. `lib/sse.ts` reads GET and POST streams with fetch and
  `ReadableStream` (not `EventSource`). In Tailwind the one accent is `accent`, secondary text is
  `text-muted-foreground`. On Windows build with `bun run build` (it fixes Next's export file
  names); the image builds with Node.

### 15.2 Keys and access

- `HACK_API_KEY` is gone. Two scoped keys replace it:
  - `HACK_BRIDGE_KEY`: accepted only on `/api/whatsapp/session`, `/api/whatsapp/pending`,
    `/api/intake/*`, `POST /api/reports/{id}/approve`, `POST /api/reports/{id}/correct`,
    `GET /api/reports/{id}`, `GET /api/reports/{id}/files/*`, `GET /api/files/*` and the
    WebSocket `/api/call/ws`.
  - `HACK_MCP_KEY`: accepted only on `/mcp`.
  A bearer key on any other route is a 401. `core/auth.require_key("bridge" | "mcp")` is the
  dependency for bearer-only routes. Tests: the MCP key on `approve` is 401.
- Open paths are exactly `GET /api/health` and `GET|POST /api/whatsapp/webhook`.
- Cookie: `__Host-gh_session` (`Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax`) outside local
  development, `gh_session` locally. Basic credentials are honoured only on GET (to set the
  cookie).
- Every non-GET request authenticated by cookie, and every WebSocket handshake authenticated by
  cookie, must carry an `Origin` equal to `PUBLIC_BASE_URL` (in dev also `http://localhost:3000`),
  else 403 or close code 4403. Bearer requests are exempt.
- The server refuses to start when `HACK_AUTH_DISABLED=1` or `HACK_DEV=1` while `PUBLIC_BASE_URL`
  is not a `http://localhost` or `http://127.0.0.1` origin, when `HACK_SESSION_SECRET` is shorter
  than 32 characters, or when a key is empty. `GET /api/health` returns
  `{status, auth: boolean, features}`.
- The access log drops the query string for `/api/whatsapp/*`.

### 15.3 Limits (`core/limits.py`)

- At most 3 calls at once; a fourth gets `{"type":"error","message":...}` and is closed.
- A call ends after 6 minutes, or after 60 s without caller speech.
- At most 40 model-backed requests per session cookie per 10 minutes; then 429 with a Finnish
  message. `limits.model_call(request)` is the dependency every model-backed route uses.
- `HACK_MODELS_OFF=1` answers every model-backed route with 503 (`models_off`).
- Mail: at most 10 messages per hour (`MAIL_MAX_PER_HOUR`).
- Uploads, enforced while the request streams in: audio at most 25 MB and 10 minutes; documents
  at most 15 MB each and 10 per run; `content_base64` at most 15 MB decoded.

### 15.4 Files

- Accept audio only by magic bytes (ogg, webm, mp3, wav, m4a) and documents only as PDF, DOCX or
  TXT. Store each file under a server-generated name; never join a client-supplied name into a
  path; look names up in the owning record.
- Serve stored files with an explicit content type from that allowlist,
  `X-Content-Type-Options: nosniff` and `Content-Disposition: attachment`.
- Before audio goes to gaik's Transcriber, convert it with ffmpeg using the detected input format
  (`ffmpeg -f <format> -i in -ar 16000 -ac 1 out.wav`).
- User-made report types and Studio fields are stored as `FieldDef` JSON and rebuilt with
  `create_extraction_model`. Never use gaik `save_schema` or `load_schema` (they execute Python).

### 15.5 Shared gaik operations (`core/gaik_ops.py`, foundation)

One tested wrapper per operation; reports, studio and mcp call these instead of writing their own:

```python
async def parse_document(path: Path) -> ParsedDocument            # .text, .pages (list[str]) for PDF, DOCX, TXT
async def transcribe_audio(path: Path, language: str = "fi") -> Transcript   # .text, .duration_s
async def generate_fields(requirement: str) -> list[FieldDef]     # gaik SchemaGenerator, mapped to FieldDef
async def extract_fields(text: str, fields: list[FieldDef], requirement: str, *, trace: bool = True) -> list[FieldValue]
                                                                  # gaik DataExtractor (+ SourceTracing for evidence)
async def classify_jev(text: str, labels: dict[str, str], instructions: str | None = None) -> Classification
async def classify_azure(text: str, labels: dict[str, str], instructions: str | None = None) -> Classification
                                                                  # Classification: label, confidence, probabilities, model
async def embed_texts(texts: list[str]) -> list[list[float]]      # gaik Embedder
```

Each returns typed results, raises `AppError` with a Finnish message on failure, and records its
duration. `extract_fields` builds the schema from `FieldDef` without a model call.

### 15.6 Data leaving the EU

Jev (TypeSafe, United States) is used only where the input is a sample or a typed description:
the Studio labels template and the AI Act tier. Spoken content never goes to Jev:

- Reports, steps `classify` (type) and `urgency`, use `classify_azure` for every channel.
- Jev inputs are capped: 2 000 characters.
- Every surface that takes free text or speech shows one line:
  "Älä kerro henkilötietoja." The AI Act page and the Studio labels template add
  "Kuvaus käsitellään myös Yhdysvalloissa." The MCP tool descriptions of `classify` and
  `assess_ai_system` say the same in English.
- The "Näin tämä on tehty" panel names where each step runs.

### 15.7 Reports (changes to 8.1 and 8.2)

- `reports.service` is the only way other features touch reports. Every function is `async def`:

```python
async def list_report_types() -> list[ReportType]
async def get_report_type(type_id: str) -> ReportType | None
async def create_report(type_id: str, channel: Channel, caller_label: str, status: ReportStatus = "processing") -> Report
async def get_report(report_id: str) -> Report | None
async def list_reports(limit: int = 50) -> list[ReportSummary]
async def set_fields(report_id: str, values: dict[str, str | None], source: str) -> Report
async def add_transcript(report_id: str, role: str, text: str) -> None
async def add_event(report_id: str, kind: str, label: str, *, detail: str | None = None, component: str | None = None, status: str = "done", ms: int | None = None) -> TimelineEvent
async def finish_call(report_id: str) -> None        # starts steps 3 to 6 on the call transcript as a tracked background task
async def fail_report(report_id: str, detail: str) -> None
async def intake_text(text: str, *, type_id: str | None = None, channel: Channel = "text", caller_label: str = "Teksti", wait: bool = True) -> tuple[Report, str]
async def intake_audio(path: Path, *, type_id: str | None = None, channel: Channel = "upload", caller_label: str = "Äänitiedosto", wait: bool = True) -> tuple[Report, str]
async def approve(report_id: str, to: str | None = None) -> Report
async def correct(report_id: str, text: str) -> Report
```

- `POST /api/intake/audio|text` accept `?wait=false`: answer at once with the report in status
  `processing`; progress arrives on the streams. The browser uses `wait=false`, the bridge the
  default.
- Background work is a tracked task that does not depend on a request or socket.
- A call that ends without `save_report`: with any field set, run steps 3 to 6 on the transcript
  so far; otherwise `failed` with the detail "Puhelu katkesi".
- `MAIL_DEFAULT_TO` (must be in `MAIL_ALLOWED_TO`; default: its first entry). `approve` without
  `to` uses it; `GET /api/mail/recipients` returns `{recipients, default, enabled}`. With no
  allowlisted recipient: 409 `mail_no_recipient`.
- `approve` moves `needs_approval` to `sending` to `sent` in one store update; any other status is
  409 `already_sent`. Approving twice sends one mail (test it).
- The mail is a fixed template: report title, field labels and values as plain text, URLs removed
  from values, the Word file attached. No model-written text, no HTML.
- `DELETE /api/reports/{id}` and `DELETE /api/report-types/{id}` (non-builtin only).
  `server/scripts/reset_demo.py` clears `DATA_DIR`.
- The start frame of the call WebSocket may carry `channel` and `caller_label`; they are honoured
  only for a bearer-authenticated socket (the bridge uses `channel: "whatsapp"`).
- A text frame makes the relay (1) emit `transcript` (role caller, final), (2) send
  `conversation.item.create` with `input_text`, (3) send `response.create`, or queue it until the
  running response is done. The same deferral applies after every tool output.

### 15.8 AI Act (changes to 8.4)

- Unchanged by the 2026 amendment: Annex III, Article 5(1)(a) to (h), Article 6(1) to (8),
  Article 50(1) to (6). Changed: Article 5 gains (ba), (bb), 1a and 1b; Article 6 gains 1a to 1c;
  Article 50(7) is replaced; Article 4 and the dates of Article 113 are new.
- `Assessment` gains `also_transparency: boolean`, `corpus_version: string`
  (`"02024R1689-20260727"`), `derogation_note: string | null` (Annex III cases point to
  Article 6(3)); `Obligation` gains `applies_from: string` (a date from Article 113 as amended).
- The server stores each assessment. `POST /api/ai-act/report` takes
  `{ "assessment_id": string }`, never an assessment from the client.
- `app/aiact/service.py` exposes `async def assess(description: str, on_step=None) -> Assessment`,
  `async def search(q: str, k: int = 8) -> list[SearchHit]` and
  `async def get_unit(unit_id: str) -> Unit | None`. The SSE endpoint and the MCP tools use them.
- The page says "todennäköinen riskiluokka" and "ei oikeudellista neuvontaa"; when
  `needs_human_review` is true that notice stands above the tier.

### 15.9 Studio (changes to 8.3)

- The exported script holds the spec only as one data literal
  (`SPEC = json.loads(<repr of the JSON>)`); every user or model string is read from `SPEC` at run
  time, never inserted into code. A test exports a spec whose description contains `"""` and
  `import os` and checks the result with `ast`.

### 15.10 MCP (changes to 8.5)

- Register with `lifecycle.register("mcp", context=lambda: mcp.session_manager.run())` and
  `lifecycle.mount_asgi("/mcp", mcp.streamable_http_app(stateless_http=True, json_response=True, transport_security=...))`
  with the public host in `allowed_hosts`. Tools are `async` and call `core/gaik_ops`,
  `reports.service` and `aiact.service`.
- The activity `summary` holds the tool name and counts only, never argument text. There is no
  approve tool and no tool that sends mail, fetches a URL or reads a path.

### 15.11 WhatsApp (changes to 9)

- `POST /api/whatsapp/webhook` stores only while a bridge session is active and only bodies whose
  `X-Hub-Signature-256` matches `^sha256=[0-9a-f]{64}$`; otherwise it answers 200 and drops them.
  The queue lives in process memory only: never in SQLite, never logged, cleared on session end
  and expiry. `pending` returns `body_b64`, the base64 of the exact bytes received, and the
  signature header verbatim.
- The phone number passes through the API only inside the raw webhook body in that queue; the
  API never parses, stores or logs it. Intake calls carry `caller_label="WhatsApp"` only.
- The bridge probes its own handshake through the public URL before subscribing the Meta app,
  and de-duplicates by message id across restarts (a small local file).
- The Meta test number was claimed on 12.11.2026: phone number id `1491262427399689`, WhatsApp
  Business Account id `4624332917843413` (not secrets). The bridge reads them from
  `bridge/config.json` (falling back to the env library names), because the library still holds
  the old ids.
- **Calls (experimental).** The owner wants to call the number. The bridge enables calling on the
  number (`POST /{phone-number-id}/settings`), subscribes the `calls` webhook field as well, and
  on a `connect` event answers with a local WebRTC peer (aiortc): it accepts the SDP offer, sends
  `pre_accept` and `accept` through the Graph API, and bridges audio both ways to the app's own
  call WebSocket (`/api/call/ws`, bearer `HACK_BRIDGE_KEY`, `channel: "whatsapp"`), resampling
  between 48 kHz Opus frames and 24 kHz PCM16. A call therefore uses the same agent and produces
  the same report as a browser call. It cannot be tested without a real call from a verified
  phone: build it behind `--calls`, test the audio bridge with synthetic frames, and say plainly
  in the report what was never run.

### 15.12 Deployment (changes to 12)

- Probes use `GET /api/health`. The container runs
  `python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1 --proxy-headers --forwarded-allow-ips=* --timeout-graceful-shutdown 10`.
- The Deployment sets `PUBLIC_BASE_URL=https://gaik-hackathon.2.rahtiapp.fi`, `MAIL_FROM`,
  `MAIL_ALLOWED_TO`, `MAIL_DEFAULT_TO`, `SMTP_HOST=smtp.pouta.csc.fi`, `SMTP_PORT=25`,
  `GAIK_DECIDE_URL=http://gaik-decide:8080` and the deployment names `AZURE_DEPLOYMENT`,
  `AZURE_TRANSCRIPTION_MODEL`, `EMBEDDING_MODEL`.
- Realtime: `AZURE_REALTIME_ENDPOINT` from `voice-agent-secrets:AZURE_ENDPOINT`,
  `AZURE_REALTIME_API_KEY` from `:AZURE_API_KEY`, `AZURE_REALTIME_DEPLOYMENT` from
  `:AZURE_REALTIME2_DEPLOYMENT`.
- The app secret is created from named files with an explicit key list that leaves out
  `HACK_AUTH_DISABLED` and `HACK_DEV`; never from the whole `.env`.
- Label `app.kubernetes.io/part-of: mini-hackathon` on every object. Where
  `.work/research/deploy.md` differs from this contract, the contract wins.
- After the event: rotate `HACK_MCP_KEY`, `HACK_SESSION_SECRET` and the Basic password.
