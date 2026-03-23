# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.sessions.external_session_binding_models import (
        ExternalSessionBinding,
    )
    from agent_teams.sessions.external_session_binding_repository import (
        ExternalSessionBindingRepository,
    )
    from agent_teams.sessions.session_rounds_projection import (
        approvals_to_projection,
        build_session_rounds,
        find_round_by_run_id,
        paginate_rounds,
    )
    from agent_teams.sessions.session_models import SessionMode, SessionRecord
    from agent_teams.sessions.session_repository import SessionRepository
    from agent_teams.sessions.session_service import SessionService

__all__ = [
    "SessionRecord",
    "SessionMode",
    "ExternalSessionBinding",
    "ExternalSessionBindingRepository",
    "SessionRepository",
    "SessionService",
    "approvals_to_projection",
    "build_session_rounds",
    "find_round_by_run_id",
    "paginate_rounds",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "SessionRecord": ("agent_teams.sessions.session_models", "SessionRecord"),
    "SessionMode": ("agent_teams.sessions.session_models", "SessionMode"),
    "ExternalSessionBinding": (
        "agent_teams.sessions.external_session_binding_models",
        "ExternalSessionBinding",
    ),
    "ExternalSessionBindingRepository": (
        "agent_teams.sessions.external_session_binding_repository",
        "ExternalSessionBindingRepository",
    ),
    "SessionRepository": (
        "agent_teams.sessions.session_repository",
        "SessionRepository",
    ),
    "SessionService": ("agent_teams.sessions.session_service", "SessionService"),
    "approvals_to_projection": (
        "agent_teams.sessions.session_rounds_projection",
        "approvals_to_projection",
    ),
    "build_session_rounds": (
        "agent_teams.sessions.session_rounds_projection",
        "build_session_rounds",
    ),
    "find_round_by_run_id": (
        "agent_teams.sessions.session_rounds_projection",
        "find_round_by_run_id",
    ),
    "paginate_rounds": (
        "agent_teams.sessions.session_rounds_projection",
        "paginate_rounds",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
