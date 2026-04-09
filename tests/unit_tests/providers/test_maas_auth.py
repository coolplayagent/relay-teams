# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import httpx

from relay_teams.providers.maas_auth import build_maas_openai_client
from relay_teams.providers.model_config import MaaSAuthConfig


def test_build_maas_openai_client_disables_sdk_retries() -> None:
    http_client = httpx.AsyncClient()
    try:
        client = build_maas_openai_client(
            base_url="https://maas.example/api/v2",
            auth_config=MaaSAuthConfig(
                username="relay-user",
                password="relay-password",
            ),
            default_headers=None,
            http_client=http_client,
            connect_timeout_seconds=15,
            ssl_verify=None,
        )
        assert client.max_retries == 0
    finally:
        asyncio.run(http_client.aclose())
