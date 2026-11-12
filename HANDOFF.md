# Handoff

State on 12.11.2026, about 15:00. Built in one afternoon for the GAIK mini-hackathon; a demo, not a
production service.

## What is running

- **App:** https://gaik-hackathon.2.rahtiapp.fi (CSC Rahti, namespace `gaik`, group label
  `app.kubernetes.io/part-of: mini-hackathon`). Login: Basic auth; user and password are
  `HACK_BASIC_USER` / `HACK_BASIC_PASSWORD` in the repo `.env` and in the library file
  `haaga-helia/gaik/gaik-hackathon.env`.
- **Local:** API on http://localhost:8787 (`uv run python -m uvicorn app.main:app --port 8787` from
  `server/`), web dev server on http://localhost:3000 (`bun run dev` from `web/`). Auth is off locally.
- **Deploy:** commit, then `bash deploy/deploy.sh` from Git Bash (builds on Rahti from git HEAD, about
  three minutes, then rolls out). The pod restarts on every deploy: a running WhatsApp bridge must be
  restarted afterwards.

## The four demos

| Page | What it does | Checked |
| --- | --- | --- |
| `/soita` Call in a report | Voice agent fills a report; live board; Word file; approve and email | Typed call end to end against the live local API; a call with synthesised speech through the relay. A real microphone in a browser was not tried |
| `/studio` GAIK Studio | Describe a use case, get a pipeline of gaik components, run it on samples | Server: all four templates ran live on the samples. Page built; not looked at by the integrator beyond its first view |
| `/ai-act` AI Act checker | Risk tier, basis with verified quotes, obligations with dates | Server: 37 of 37 evaluation cases on the first run. Page built; only its first view was looked at by the integrator |
| `/tyokalut` Tools for agents | The components as MCP tools, live activity log | Page and all nine tools ran against the local API; no real agent client was connected |

The interface is English; `web/lib/api.ts` still
holds a few Finnish error messages.

## WhatsApp

- Demo number **+1 555 150 8387** (Meta test number of the app `lumicode-wa-dev`, claimed 12.11.2026).
  The owner's work phone is a verified recipient. Ids are in `bridge/config.json` and in
  `personal/lajio.env`.
- Start the bridge on the owner's machine (keys are read into the process only):

  ```powershell
  cd bridge
  .\run.ps1 -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi -Allow <work number, digits only> -Calls
  ```

  `-Allow` is needed because `LUMI_ALLOWED` in the env library holds another number. Stop with Ctrl+C,
  or `.\run.ps1 -Stop -PublicBaseUrl https://gaik-hackathon.2.rahtiapp.fi` if the window was closed.
- `dotenvx` sometimes fails to decrypt a key four times in a row; run the command again.
- Messages: text or a voice note becomes a report; the reply carries the summary, the Word file and
  two buttons. Calls: `-Calls` answers a WhatsApp call from the phone app (not WhatsApp Web) and
  connects it to the same voice agent. See `bridge/README.md` and `bridge/CALLS.md`.
- **Run for real on 12.11.2026:** a text message from WhatsApp Web got the summary, the Word file and
  the two buttons back in about half a minute; a call from the owner's phone was answered and lasted
  85 s with 0 underruns, and produced a report on the board (channel "WhatsApp call").
- **Not tried:** a voice note from a phone, the "Approve and send" and "Correct" buttons, and stopping
  the bridge with Ctrl+C or `-Stop`.

## What was cut or left

- No trailer.
- AI Act cites the text of 12.7.2024 for provisions that Regulation (EU) 2026/1744 left unchanged
  and takes application dates from the amendment; there is no parser for the consolidated text.
- Mail is sent only from Rahti, only to the two allowlisted Haaga-Helia addresses, only after
  "Approve and send". Real delivery was not tested.
- No adversarial code review pass was run after the features were built.
- Two foundation tests in `server/tests/core/test_app.py` fail since the real report routes replaced
  their echo routes.
- Not registered yet: `~/.agents/config/repos.tsv`, `env-map.json`, and the service in
  `~/.agents/workspace/hh/gaik/DEPLOYMENTS.md`. There is no GitHub remote.
- After the event: rotate `HACK_MCP_KEY`, `HACK_SESSION_SECRET` and the Basic password, or scale the
  deployment to zero (`oc scale deploy/gaik-hackathon --replicas=0 -n gaik`).

## Where to read

`docs/ARCHITECTURE.md` is the contract (section 15 overrides the rest). `.work/research/` holds the
maps the contract was written from (not in git).
