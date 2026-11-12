# WhatsApp calls (experimental)

With `--calls` the bridge also answers voice calls to the demo number. A call uses the same agent
as the browser call and ends in the same report: the bridge answers WhatsApp's WebRTC offer with
a local peer (aiortc) and connects the audio to the app's call WebSocket (`/api/call/ws`, bearer
bridge key, `channel: "whatsapp"`). Code: `calls.py` (signalling and the call), `call_audio.py`
(resampling and pacing), `tests/test_calls.py`.

## Run

```powershell
$env:BRIDGE_CALLS = '1'      # or: -Calls, once run.ps1 passes --calls on
.\run.ps1 -PublicBaseUrl https://<app>
```

`python main.py --calls` does the same when the keys are already in the environment. At start the
bridge switches calling on for the number (`POST /{phone-number-id}/settings`; it stays on after
the run) and subscribes the webhook fields `messages,calls`. The log shows `call ...: connected`,
or why a call did not connect, and at the end the seconds spoken, the seconds heard and the
underrun count.

Optional keys in `config.json`: `call_type_id` (default `turvallisuushavainto`), `call_language`
(`fi`, `en` or `sv`; default `en`), `call_ice_servers` (default one public STUN server, which sees
the laptop's public address and nothing else).

## On the phone

1. Open the chat with **+1 555 150 8387** in the WhatsApp app on an iPhone or Android phone whose
   number is on the allowed list and on the test number's recipient list.
2. If the call icon is missing, open the chat info once and go back.
3. Call. The agent greets within a few seconds; talk as in the browser call. Hang up when done,
   or let the agent save the report: the bridge then ends the call after its last words.

WhatsApp Web and the desktop apps cannot call a Cloud API number. Use earbuds on the first try:
whether the agent hears itself through the phone's speaker is not known.

## How the audio moves

| Direction | Path |
| --- | --- |
| Caller to agent | Opus 48 kHz, decoded by aiortc, resampled with `av` to PCM16 mono 24 kHz, sent to the app in 40 ms binary frames |
| Agent to caller | the app's PCM16 24 kHz frames go into a buffer; the WebRTC track hands out 20 ms frames at real time, and aiortc's Opus encoder takes them to 48 kHz |

Speech starts once a quarter second is buffered and a frame is never padded with silence in the
middle of speech; a frame that had to wait counts as an underrun. `{"type":"interrupt"}` from the
app drops everything queued. `{"type":"saved"}` or a closed socket ends the call after the queue
has played. A `terminate` event from Meta (the caller hung up) sends `{"type":"stop"}` to the app
and closes the peer. Calls from numbers that are not allowed are rejected; one call at a time.

## Tested here

`uv run --with pytest python -m pytest tests/test_calls.py -q` (about 30 s, no network beyond
127.0.0.1):

- resampling both ways keeps duration, level and pitch of a 440 Hz tone; stereo becomes mono;
- the buffer: quarter-second start, whole frames only, underrun count, interrupt, order;
- pacing: 20 ms per frame without drift, and the track at real time;
- the answer to Meta's sample offer (ICE-lite server, Opus): valid SDP, active DTLS role,
  controlling ICE role;
- a whole call in one process, with a local WebRTC peer as the phone, a mocked Graph API and a
  local WebSocket server as the app: `pre_accept` then `accept`, the bearer header and the start
  frame, audio both ways, an interrupt that silences five queued seconds, the app ending the call
  after its last words, the caller hanging up, the bridge closing, media that never connects;
- the access token is sent only to `graph.facebook.com`; importing `calls` loads no aiortc.

## Only a real call can show

- that Meta accepts `pre_accept` and `accept` for this number and token, and that calling is
  really on for it (nothing here touched the number's settings or the webhook);
- that ICE pairs this machine's address with Meta's media server on the network in use;
- the time to answer, the latency, barge-in, echo without earbuds, and how the agent's voice
  sounds through the phone (an earlier prototype of the same technique broke up at times before
  the quarter-second start was added; the underrun count in the log tells whether it still does);
- the app side end to end: the tests used a stand-in for `/api/call/ws`, not the deployed app.
