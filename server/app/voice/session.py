"""The Realtime session of one call: where to connect, what the agent is told, its two tools.

Everything here is pure (no network), so the payload and the prompt are unit-tested.

    url, headers = realtime_target(settings)
    payload = session_update(report_type, "en")            # GA shape, section 13
    greeting = greeting_request(report_type, "en")         # a scripted `response.create`

`SESSION_VARIANTS` are tried in order when the deployment refuses a `session.update`
(`gpt-4o-transcribe` first, then `whisper-1`, then without the optional extras).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.models import FieldDef, ReportType
from app.core.settings import Settings

VOICE = "cedar"
SAMPLE_RATE = 24_000
VAD = {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300, "silence_duration_ms": 700}

LANGUAGES: dict[str, str] = {"en": "English", "fi": "Finnish", "sv": "Swedish"}
DEFAULT_LANGUAGE = "en"

SET_FIELDS = "set_fields"
SAVE_REPORT = "save_report"

# (transcription model, send `reasoning`, send `noise_reduction`), tried in this order.
SESSION_VARIANTS: tuple[tuple[str, bool, bool], ...] = (
    ("gpt-4o-transcribe", True, True),
    ("whisper-1", True, True),
    ("whisper-1", False, False),
)

_GREETINGS: dict[str, str] = {
    "en": (
        "Hello, I'm the AI assistant of GAIK. I'll write up your report: {title}. "
        "Tell me about it in your own words."
    ),
    "fi": (
        "Hei, olen GAIKin tekoälyavustaja. Kirjaan raporttisi. Kerro omin sanoin, mistä on kyse."
    ),
    "sv": (
        "Hej, jag är GAIK:s AI-assistent. Jag skriver din rapport. "
        "Berätta med egna ord vad det gäller."
    ),
}


def _today() -> date:
    """Today in Finland. Without a time zone database (Windows) fall back to UTC+2."""
    try:
        zone: timezone | ZoneInfo = ZoneInfo("Europe/Helsinki")
    except ZoneInfoNotFoundError:
        zone = timezone(timedelta(hours=2))
    return datetime.now(zone).date()


def language_of(value: object) -> str:
    """The call language from the start frame: `en`, `fi` or `sv`; anything else is English."""
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def realtime_target(settings: Settings) -> tuple[str, dict[str, str]]:
    """The GA WebSocket URL and its headers. Raises `RuntimeError` naming a missing setting."""
    missing = [
        name
        for name, value in (
            ("AZURE_REALTIME_ENDPOINT", settings.azure_realtime_endpoint),
            ("AZURE_REALTIME_API_KEY", settings.azure_realtime_api_key),
            ("AZURE_REALTIME_DEPLOYMENT", settings.azure_realtime_deployment),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing settings: " + ", ".join(missing))
    endpoint = settings.azure_realtime_endpoint.strip()
    host = urlsplit(endpoint if "//" in endpoint else f"https://{endpoint}").netloc
    deployment = quote(settings.azure_realtime_deployment, safe="")
    url = f"wss://{host}/openai/v1/realtime?model={deployment}"
    return url, {"api-key": settings.azure_realtime_api_key}


def greeting_text(report_type: ReportType, language: str) -> str:
    return _GREETINGS[language_of(language)].format(title=report_type.title)


def greeting_request(report_type: ReportType, language: str) -> dict[str, Any]:
    """A `response.create` that makes the agent say the scripted greeting and nothing else."""
    sentence = greeting_text(report_type, language)
    return {
        "type": "response.create",
        "response": {
            "instructions": (
                "Say exactly the following greeting, word for word, and nothing else. "
                f"Do not call a tool.\n\n{sentence}"
            ),
            "tool_choice": "none",
        },
    }


def _field_line(field: FieldDef) -> str:
    parts = [f"- {field.name} ({field.label}; {field.type}"]
    parts.append("; required" if field.required else "; optional")
    line = "".join(parts) + f"): {field.description}"
    if field.options:
        line += " One of: " + " | ".join(field.options) + "."
    return line


def instructions(report_type: ReportType, language: str, today: date | None = None) -> str:
    """The system prompt. The language rule comes first: it is the one the model must not drop."""
    today = today or _today()
    name = LANGUAGES[language_of(language)]
    required = [field.label for field in report_type.fields if field.required]
    fields = "\n".join(_field_line(field) for field in report_type.fields)
    return f"""# Language (the most important rule)
Speak only {name} for the whole call, also in short phrases before a tool call, whatever
language the caller uses. Never switch language.

# Role
You are the AI assistant of GAIK. You take a report by phone: "{report_type.title}".
{report_type.description}
You only take the report. You give no advice and you do not chat about other things.

# How the call goes
1. The greeting has been said. Let the caller tell it in their own words. Do not interrupt.
2. Every time you hear a value for a field, call `{SET_FIELDS}` with it straight away, before
   you speak. Send only the fields you heard in the latest turn or that changed.
3. Then ask for ONE missing required field, in one short question. Never ask for two things at
   once. Never ask for something the caller already said. The tool result names what is missing.
4. When no required field is missing, read back the report in ONE sentence and ask if it is right.
5. Only after the caller says it is right, call `{SAVE_REPORT}`. If they correct something, call
   `{SET_FIELDS}` with the correction and read the sentence back again.
6. After `{SAVE_REPORT}` succeeds, thank the caller in one short sentence and say goodbye.

# Rules
- Keep every turn short: one or two sentences. No lists, no repetition of what was said.
- Never invent, guess or assume a value. Record only what the caller said. If the caller says
  they do not know a required value, record "Not known" for it and move on; never suggest
  that answer yourself.
- Write field values in English, whatever language the call is in. Dates as YYYY-MM-DD and
  times as HH:MM. Today is {today:%A} {today.isoformat()}; work out "today" and "yesterday" from it.
- Never ask the caller for a format or an option list: take what they say and convert it.
- For a field with listed options, the value is exactly one of the options.
- Do not ask for names, phone numbers or other personal data, and do not record them.
- If the caller asks, say plainly that you are an AI assistant.

# Fields of this report
{fields}

Required: {", ".join(required) if required else "none"}.
"""


def _field_schema(field: FieldDef) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": f"{field.label}. {field.description}",
    }
    if field.type == "choice" and field.options:
        schema["enum"] = list(field.options)
    return schema


def tools(report_type: ReportType) -> list[dict[str, Any]]:
    """`set_fields` (values as they are heard) and `save_report` (after the caller confirms)."""
    return [
        {
            "type": "function",
            "name": SET_FIELDS,
            "description": (
                "Record field values the caller just said. Call it as soon as a value is heard "
                "and again when a value is corrected. Leave out fields you did not hear."
            ),
            "parameters": {
                "type": "object",
                "properties": {field.name: _field_schema(field) for field in report_type.fields},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": SAVE_REPORT,
            "description": (
                "Save the report. Call it only after you read back a one-sentence summary and "
                "the caller confirmed it is right."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The one-sentence summary the caller confirmed.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    ]


def session_update(report_type: ReportType, language: str, variant: int = 0) -> dict[str, Any]:
    """The GA-shaped `session.update` for this call. `variant` indexes `SESSION_VARIANTS`."""
    language = language_of(language)
    transcription_model, with_reasoning, with_noise_reduction = SESSION_VARIANTS[variant]
    audio_input: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
        "transcription": {"model": transcription_model, "language": language},
        "turn_detection": dict(VAD),
    }
    if with_noise_reduction:
        audio_input["noise_reduction"] = {"type": "far_field"}
    session: dict[str, Any] = {
        "type": "realtime",
        "instructions": instructions(report_type, language),
        "output_modalities": ["audio"],
        "audio": {
            "input": audio_input,
            "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": VOICE},
        },
        "tools": tools(report_type),
        "tool_choice": "auto",
    }
    if with_reasoning:
        session["reasoning"] = {"effort": "low"}
    return {"type": "session.update", "session": session}
