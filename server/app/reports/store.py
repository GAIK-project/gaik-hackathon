"""Report documents in the store, and the two event topics every change is published on.

A stored report is the `Report` JSON plus one private key, `x`: the names of the required
fields, the stored file names and the single-use approval claim. `x` never leaves the server.

Every change goes through `mutate`, which recomputes what is derived (filled, total, missing,
headline, updated_at), stores the document in one step and publishes it:

- topic `reports`: event `report` with the `ReportSummary` (and `deleted` with `{"id"}`),
- topic `report:<id>`: event `report` with the full `Report`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.core.db import get_store
from app.core.errors import AppError
from app.core.events import get_bus
from app.core.models import FieldValue, Report, ReportType, TimelineEvent
from app.core.util import new_id, now_iso

COLLECTION = "reports"
TOPIC = "reports"
HEADLINE_MAX = 110
PENDING_TYPE_TITLE = "Identifying report type"


def topic_of(report_id: str) -> str:
    return f"report:{report_id}"


def scope_of(report_id: str) -> str:
    return f"reports/{report_id}"


def not_found() -> AppError:
    return AppError("not_found", "There is no such report.", 404)


def _one_line(text: str, limit: int = HEADLINE_MAX) -> str:
    line = re.sub(r"\s+", " ", text).strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _headline(data: dict[str, Any]) -> str:
    """One line from the most telling field: the first filled long text, else the first filled
    field that is not a date or a time, else the type's title."""
    kinds: dict[str, str] = data["x"].get("kinds", {})
    filled = [field for field in data["fields"] if field.get("value")]
    for wanted in (("longtext",), ("text", "choice"), ("number", "date", "time")):
        for field in filled:
            if kinds.get(field["name"], "text") in wanted:
                return _one_line(field["value"])
    return data.get("type_title") or ""


def derive(data: dict[str, Any]) -> None:
    required: list[str] = data["x"].get("required", [])
    by_name = {field["name"]: field for field in data["fields"]}
    wanted = [by_name[name] for name in required if name in by_name]
    data["total"] = len(wanted)
    data["filled"] = sum(1 for field in wanted if field.get("value"))
    data["missing"] = [field["label"] for field in wanted if not field.get("value")]
    data["headline"] = _headline(data)
    data["updated_at"] = now_iso()


def publish(report: Report) -> None:
    bus = get_bus()
    bus.publish(TOPIC, "report", report.summary())
    bus.publish(topic_of(report.id), "report", report)


def empty_fields(rtype: ReportType) -> list[dict[str, Any]]:
    return [
        FieldValue(
            name=field.name, label=field.label, value=None, evidence=None, source="extractor"
        ).model_dump()
        for field in rtype.fields
    ]


def type_keys(rtype: ReportType) -> dict[str, Any]:
    return {
        "required": [field.name for field in rtype.fields if field.required],
        "kinds": {field.name: field.type for field in rtype.fields},
    }


def create(data: dict[str, Any]) -> Report:
    data.setdefault("x", {})
    derive(data)
    get_store().put(COLLECTION, data["id"], data)
    report = Report.model_validate(data)
    publish(report)
    return report


def load(report_id: str) -> Report | None:
    doc = get_store().get(COLLECTION, report_id)
    return Report.model_validate(doc.data) if doc else None


def load_raw(report_id: str) -> dict[str, Any] | None:
    doc = get_store().get(COLLECTION, report_id)
    return doc.data if doc else None


def mutate(report_id: str, change: Callable[[dict[str, Any]], Any]) -> Report:
    """Change one report in a single store step, then publish it. `change` edits the stored
    dict in place; it may raise `AppError` to refuse, and then nothing is stored."""

    def apply(data: dict[str, Any]) -> None:
        data.setdefault("x", {})
        change(data)
        derive(data)

    doc = get_store().update(COLLECTION, report_id, apply)
    if doc is None:
        raise not_found()
    report = Report.model_validate(doc.data)
    publish(report)
    return report


def delete(report_id: str) -> bool:
    removed = get_store().delete(COLLECTION, report_id)
    if removed:
        get_bus().publish(TOPIC, "deleted", {"id": report_id})
        get_bus().publish(topic_of(report_id), "deleted", {"id": report_id})
    return removed


def list_all(limit: int | None = 50) -> list[Report]:
    return [Report.model_validate(doc.data) for doc in get_store().list(COLLECTION, limit=limit)]


# timeline -----------------------------------------------------------------------------------


def new_event(
    kind: str,
    label: str,
    *,
    detail: str | None = None,
    component: str | None = None,
    status: str = "done",
    ms: int | None = None,
) -> TimelineEvent:
    return TimelineEvent.model_validate(
        {
            "id": new_id("ev"),
            "at": now_iso(),
            "kind": kind,
            "label": label,
            "detail": detail,
            "component": component,
            "status": status,
            "ms": ms,
        }
    )


def append_event(data: dict[str, Any], event: TimelineEvent) -> None:
    data.setdefault("timeline", []).append(event.model_dump())


def add_event(report_id: str, event: TimelineEvent) -> Report:
    return mutate(report_id, lambda data: append_event(data, event))


def change_event(data: dict[str, Any], event_id: str, **changes: Any) -> None:
    for event in data.get("timeline", []):
        if event["id"] == event_id:
            event.update({key: value for key, value in changes.items() if value is not ...})
            return
