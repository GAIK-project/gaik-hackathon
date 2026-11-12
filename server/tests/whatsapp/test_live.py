"""Live, read-only: the Meta token can read the number and the account's subscribed apps.

Needs `WHATSAPP_ACCESS_TOKEN` in the environment, which only `bridge/run.ps1` loads
(`bridge/run.ps1 -Status` runs the same two GETs). Nothing is sent or changed.
"""

from __future__ import annotations

import os

import httpx
import pytest

from tests.whatsapp.test_bridge import bridge_main

pytestmark = pytest.mark.live


async def test_token_reads_the_number_and_the_subscribed_apps():
    if not os.environ.get("WHATSAPP_ACCESS_TOKEN"):
        pytest.skip("WHATSAPP_ACCESS_TOKEN is not loaded; use bridge/run.ps1 -Status")
    config = bridge_main.load_config(need=())
    async with httpx.AsyncClient(timeout=30) as client:
        graph = bridge_main.Graph(config, client)
        assert (await graph.phone_number())["id"] == config.phone_number_id
        assert isinstance(await graph.subscribed_apps(), list)
