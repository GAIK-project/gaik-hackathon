# gaik-hackathon agent instructions

GAIK mini-hackathon demos: one app, four demos built on the `gaik` library. Private working repo
of the Haaga-Helia GAIK project.

- The contract is `docs/ARCHITECTURE.md`: layout, owners, API, types, design rules. Read it before
  changing anything and keep it true.
- All content is synthetic. Never add customer material, personal data, phone numbers or text
  from another project's private repo.
- No secret in the repo, in a log or in a response. Local runs get their environment from the
  owner's env library at run time; Rahti gets it from secrets.
- The gaik library is used as published on PyPI. Do not vendor or patch it here; a problem in a
  component is reported to the owner with a proposed fix, and worked around in this app.
- UI text is Finnish, sentence case, short. Code, comments and docs are English.
- Mail goes only to the addresses in `MAIL_ALLOWED_TO`, and only after a person approves.
- The WhatsApp bridge runs on the owner's machine with his personal Meta keys; those keys never
  go to Rahti or into this repo.

## checks

- Server, from `server/`: `uv run pytest -m "not live"`, `uv run ruff check .`,
  `uv run ruff format --check .`. Tests marked `live` make real model calls and need the
  environment.
- Web, from `web/`: `bunx tsc --noEmit`, `bun run lint`, `bun run build`.

## deployment

CSC Rahti, namespace `gaik`, group label `app.kubernetes.io/part-of: mini-hackathon`. Steps and
names: `deploy/README.md`.
