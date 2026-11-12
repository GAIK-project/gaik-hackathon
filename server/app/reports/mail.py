"""The mail of a report: a fixed plain-text template with the Word file attached.

No model writes any of it and there is no HTML. URLs are removed from values, so a spoken or
typed link never becomes clickable in someone's inbox. With `SMTP_HOST` set the message goes
out over SMTP (STARTTLS when the server offers it); without it the message is written as an
`.eml` file in `$DATA_DIR/outbox`.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from app.core.models import Report
from app.core.settings import get_settings
from app.reports.docx import CHANNEL_NAMES, EMPTY, urgency_name, when

DOCX_MEDIA = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
SMTP_TIMEOUT_SECONDS = 20
SUBJECT_MAX = 140
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def plain(value: str | None) -> str:
    """A value for the mail body: no URLs, no control characters."""
    text = _CONTROL.sub("", value or "").replace("\r\n", "\n").replace("\r", "\n")
    return _URL.sub("[link removed]", text).strip()


def one_line(value: str | None) -> str:
    return re.sub(r"\s+", " ", plain(value)).strip()


def subject_of(report: Report) -> str:
    title = one_line(report.type_title) or "Report"
    headline = one_line(report.headline)
    subject = f"{title}: {headline}" if headline and headline != title else title
    return subject[:SUBJECT_MAX]


def body_of(report: Report) -> str:
    lines = [one_line(report.type_title) or "Report", ""]
    lines.append(f"Received: {when(report.created_at)}")
    lines.append(f"Channel: {CHANNEL_NAMES.get(report.channel, report.channel)}")
    if name := urgency_name(report):
        lines.append(f"Urgency: {name}")
    lines.append("")
    for field in report.fields:
        value = plain(field.value) or EMPTY
        if "\n" in value:
            lines.append(f"{one_line(field.label)}:")
            lines.extend(f"  {part}" for part in value.split("\n"))
        else:
            lines.append(f"{one_line(field.label)}: {value}")
    if report.missing:
        lines += ["", "Missing: " + ", ".join(one_line(label) for label in report.missing)]
    lines += [
        "",
        "The Word report is attached.",
        "A person approved this message before it was sent. GAIK mini-hackathon demo, "
        "synthetic content.",
    ]
    return "\n".join(lines) + "\n"


def build_message(report: Report, to: str, docx: bytes | None, docx_name: str) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.mail_from or "gaik-demo@localhost"
    message["To"] = to
    message["Subject"] = subject_of(report)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=(settings.public_host or "localhost"))
    message.set_content(body_of(report))
    if docx:
        message.add_attachment(
            docx, maintype=DOCX_MEDIA[0], subtype=DOCX_MEDIA[1], filename=docx_name
        )
    return message


def send_smtp(message: EmailMessage) -> None:
    """Blocking: run it in a worker thread."""
    settings = get_settings()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.send_message(message)


def outbox_dir() -> Path:
    return get_settings().data_path / "outbox"


def write_eml(message: EmailMessage, report_id: str) -> Path:
    """Mail is off: keep the message as a file a person can open. Blocking."""
    folder = outbox_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report_id}.eml"
    path.write_bytes(message.as_bytes())
    return path
