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
   environment variables win over it). `HACK_AUTH_DISABLED=1` and `HACK_DEV=1` are for local
   development only.
2. API, from `server/`:

   ```
   uv sync
   uv run python -m uvicorn app.main:app --port 8787 --timeout-graceful-shutdown 3
   ```

   The app starts with whatever features import; `GET /api/health` lists them.
3. Front end: build `web/` to `web/out` and the API serves it at `/`. In development it runs
   on port 3000 against the API on 8787.

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
| `auth.py` | the access rules as middleware; `require_bearer` for bridge-only routes |
| `db.py` | `get_store()`: JSON documents by collection and id in SQLite |
| `files.py` | `save_file`, `open_file`, `list_files`, `public_url` under `DATA_DIR/files/<scope>/` |
| `events.py` | `get_bus()` and `sse_response(...)` for live streams |
| `llm.py` | `gaik_config()`, `run_blocking()`, `jev_classifier()`, `embedder()`, `structured_chat()` |
| `lifecycle.py` | `register(...)` for startup and shutdown work, `mount_asgi(...)` for `/mcp` |

Each module's docstring shows how to call it. Tests get a temporary `DATA_DIR`, known test
credentials and a `client` fixture from `server/tests/conftest.py`.
