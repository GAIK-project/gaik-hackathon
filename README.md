# gaik-hackathon

One web app with a hub and four demos, each built on components of the [`gaik`](https://pypi.org/project/gaik/) library:
**Soita raportti** (talk to a voice agent, a structured report lands on a live board),
**GAIK Studio** (describe a use case, a pipeline of gaik components appears and runs),
**AI Act -tarkastaja** (describe an AI system, get its risk tier and the articles behind it) and
**Työkalut agentille** (the same components as MCP tools). One FastAPI process serves the API,
the MCP endpoint and the statically exported Next.js front end. All sample content is synthetic.

The contract for everything here is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Run it

You need [uv](https://docs.astral.sh/uv/), ffmpeg on the PATH and, for the front end, bun.

1. Environment: copy `.env.example` to `.env` and fill it in (the file is gitignored; real
   environment variables win over it). Locally set `PUBLIC_BASE_URL=http://localhost:8787`.
2. API, from `server/`:

   ```
   uv sync
   uv run python -m uvicorn app.main:app --port 8787 --timeout-graceful-shutdown 3
   ```

   The app starts with whatever features import; `GET /api/health` answers
   `{status, auth, features}`.
3. Front end: build `web/` to `web/out` and the API serves it at `/`. In development it runs
   on port 3000 against the API on 8787 (`HACK_DEV=1`).

The server refuses to start (`StartupRefused`, naming the settings) when
`HACK_AUTH_DISABLED=1` or `HACK_DEV=1` is set while `PUBLIC_BASE_URL` is not a
`http://localhost` or `http://127.0.0.1` origin, when `HACK_SESSION_SECRET` is shorter than 32
characters, when a key or the Basic login is empty, when the two keys are equal, or when
`MAIL_DEFAULT_TO` is not in `MAIL_ALLOWED_TO`.

## Access and limits

Rules: `docs/ARCHITECTURE.md` sections 15.2 to 15.4.

| Who | Credential | Reaches |
| --- | --- | --- |
| Anyone | none | `GET /api/health`, `GET\|POST /api/whatsapp/webhook` |
| Browser | session cookie, set by a `GET` with the Basic login | everything; a write or a WebSocket must carry the app's own `Origin` |
| WhatsApp bridge | `Authorization: Bearer <HACK_BRIDGE_KEY>` | its session and long poll, intake, approve and correct, one report and its files, the call WebSocket |
| MCP client | `Authorization: Bearer <HACK_MCP_KEY>` | `/mcp` |

A key on any other route is a 401. The cookie is `__Host-gh_session` on a public deployment and
`gh_session` on localhost.

| Limit | Value |
| --- | --- |
| Calls at once | 3; a call ends after 6 minutes or 60 s without the caller |
| Model-backed requests | 40 per session cookie per 10 minutes, then 429; `HACK_MODELS_OFF=1` answers 503 |
| Mail | `MAIL_MAX_PER_HOUR` (10) per hour |
| Request body | refused while it streams in: 1 MB, except audio intake and corrections 26 MB, a Studio run 151 MB, `/mcp` and `/api/mcp/try` 21 MB |
| Uploads | audio (ogg, webm, mp3, wav, m4a) 25 MB and 10 minutes; documents (PDF, DOCX, TXT) 15 MB each, 10 per run; `content_base64` 15 MB decoded |

Uploads are accepted by their first bytes, stored under server-generated names and always served
as downloads. Counters live in the one server process and start from zero after a restart.

## Check it

From `server/`:

```
uv run pytest -m "not live"        # no network
uv run pytest -m live              # real, small calls; needs the environment
uv run ruff check .
uv run ruff format --check .
uv run python scripts/check_env.py # one line per service: chat, embedding, transcription, Jev, Realtime
```

`check_env.py` prints `ok` or the error class per service and never a setting's value.

## Server foundation

Features live in `server/app/<feature>/` and expose `router` in `router.py`; the foundation in
`server/app/core/` gives them:

| Module | What it is for |
| --- | --- |
| `settings.py` | `get_settings()`; loads `.env` before gaik is imported |
| `models.py`, `errors.py` | the shared wire types and `AppError(code, message, status)` |
| `auth.py` | the access rules as middleware; `require_key("bridge" \| "mcp")` for key-only routes |
| `limits.py` | `acquire_call`/`release_call`, `CallClock`, `Depends(limits.model_call)`, `reserve_mail()`, upload size guards |
| `db.py` | `get_store()`: JSON documents by collection and id in SQLite |
| `files.py` | `save_upload` (magic bytes, server-generated name), `to_wav` (ffmpeg), `save_file`, `open_file`, `list_files`, `public_url` under `DATA_DIR/files/<scope>/` |
| `events.py` | `get_bus()` and `sse_response(...)` for live streams |
| `llm.py` | `gaik_config()`, `run_blocking()`, `jev_classifier()`, `embedder()`, `structured_chat()` |
| `lifecycle.py` | `register(...)` for startup and shutdown work, `mount_asgi(...)` for `/mcp` |

Each module's docstring shows how to call it. Tests get a temporary `DATA_DIR`, known test
credentials and four clients from `server/tests/conftest.py`: `client` (a signed-in browser:
session cookie and Origin), `bridge_client`, `mcp_client` and `anon_client`.
