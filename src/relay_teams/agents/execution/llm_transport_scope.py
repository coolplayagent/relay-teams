# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.providers.provider_contracts import LLMRequest


def llm_http_client_cache_scope_for_request(request: LLMRequest) -> str:
    return ":".join(
        (
            request.run_id,
            request.session_id,
            request.task_id,
            request.instance_id,
            request.role_id,
        )
    )
