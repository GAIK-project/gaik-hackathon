"""The bridge hub is one object per process: every test starts with it empty and open."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.whatsapp.queue import get_hub


@pytest.fixture(autouse=True)
def empty_hub() -> Iterator[None]:
    hub = get_hub()
    hub.open()
    hub.end()
    yield
    hub.open()
    hub.end()
