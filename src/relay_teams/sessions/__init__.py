# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.sessions.external_session_binding_models import (
        ExternalSessionBinding,
    )
    from relay_teams.sessions.external_session_binding_repository import (
        ExternalSessionBindingRepository,
    )
    from relay_teams.sessions.session_history_marker_models import (
        SessionHistoryMarkerRecord,
        SessionHistoryMarkerType,
    )
    from relay_teams.sessions.session_history_marker_repository import (
        SessionHistoryMarkerRepository,
    )
    from relay_teams.sessions.session_rounds_projection import (
        approvals_to_projection,
        build_session_rounds,
        find_round_by_run_id,
        paginate_rounds,
    )
    from relay_teams.sessions.session_models import (
        ProjectKind,
        SessionMode,
        SessionRecord,
    )
    from relay_teams.sessions.session_metadata import (
        SESSION_METADATA_SOURCE_ICON_KEY,
        SESSION_METADATA_SOURCE_KIND_KEY,
        SESSION_METADATA_SOURCE_LABEL_KEY,
        SESSION_METADATA_SOURCE_PROVIDER_KEY,
        SESSION_METADATA_TITLE_SOURCE_KEY,
        SESSION_SOURCE_ICON_IM,
        SESSION_SOURCE_KIND_IM,
        SESSION_TITLE_SOURCE_AUTO,
        SESSION_TITLE_SOURCE_MANUAL,
    )
    from relay_teams.sessions.session_repository import SessionRepository
    from relay_teams.sessions.session_service import SessionService

__all__ = [
    "ProjectKind",
    "SessionRecord",
    "SessionMode",
    "ExternalSessionBinding",
    "ExternalSessionBindingRepository",
    "SessionHistoryMarkerRecord",
    "SessionHistoryMarkerRepository",
    "SessionHistoryMarkerType",
    "SessionRepository",
    "SessionService",
    "SESSION_METADATA_SOURCE_ICON_KEY",
    "SESSION_METADATA_SOURCE_KIND_KEY",
    "SESSION_METADATA_SOURCE_LABEL_KEY",
    "SESSION_METADATA_SOURCE_PROVIDER_KEY",
    "SESSION_METADATA_TITLE_SOURCE_KEY",
    "SESSION_SOURCE_ICON_IM",
    "SESSION_SOURCE_KIND_IM",
    "SESSION_TITLE_SOURCE_AUTO",
    "SESSION_TITLE_SOURCE_MANUAL",
    "approvals_to_projection",
    "build_session_rounds",
    "find_round_by_run_id",
    "paginate_rounds",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ProjectKind": ("relay_teams.sessions.session_models", "ProjectKind"),
    "SessionRecord": ("relay_teams.sessions.session_models", "SessionRecord"),
    "SessionMode": ("relay_teams.sessions.session_models", "SessionMode"),
    "SESSION_METADATA_SOURCE_ICON_KEY": (
        "relay_teams.sessions.session_metadata",
        "SESSION_METADATA_SOURCE_ICON_KEY",
    ),
    "SESSION_METADATA_SOURCE_KIND_KEY": (
        "relay_teams.sessions.session_metadata",
        "SESSION_METADATA_SOURCE_KIND_KEY",
    ),
    "SESSION_METADATA_SOURCE_LABEL_KEY": (
        "relay_teams.sessions.session_metadata",
        "SESSION_METADATA_SOURCE_LABEL_KEY",
    ),
    "SESSION_METADATA_SOURCE_PROVIDER_KEY": (
        "relay_teams.sessions.session_metadata",
        "SESSION_METADATA_SOURCE_PROVIDER_KEY",
    ),
    "SESSION_METADATA_TITLE_SOURCE_KEY": (
        "relay_teams.sessions.session_metadata",
        "SESSION_METADATA_TITLE_SOURCE_KEY",
    ),
    "SESSION_SOURCE_ICON_IM": (
        "relay_teams.sessions.session_metadata",
        "SESSION_SOURCE_ICON_IM",
    ),
    "SESSION_SOURCE_KIND_IM": (
        "relay_teams.sessions.session_metadata",
        "SESSION_SOURCE_KIND_IM",
    ),
    "SESSION_TITLE_SOURCE_AUTO": (
        "relay_teams.sessions.session_metadata",
        "SESSION_TITLE_SOURCE_AUTO",
    ),
    "SESSION_TITLE_SOURCE_MANUAL": (
        "relay_teams.sessions.session_metadata",
        "SESSION_TITLE_SOURCE_MANUAL",
    ),
    "ExternalSessionBinding": (
        "relay_teams.sessions.external_session_binding_models",
        "ExternalSessionBinding",
    ),
    "ExternalSessionBindingRepository": (
        "relay_teams.sessions.external_session_binding_repository",
        "ExternalSessionBindingRepository",
    ),
    "SessionHistoryMarkerRecord": (
        "relay_teams.sessions.session_history_marker_models",
        "SessionHistoryMarkerRecord",
    ),
    "SessionHistoryMarkerRepository": (
        "relay_teams.sessions.session_history_marker_repository",
        "SessionHistoryMarkerRepository",
    ),
    "SessionHistoryMarkerType": (
        "relay_teams.sessions.session_history_marker_models",
        "SessionHistoryMarkerType",
    ),
    "SessionRepository": (
        "relay_teams.sessions.session_repository",
        "SessionRepository",
    ),
    "SessionService": ("relay_teams.sessions.session_service", "SessionService"),
    "approvals_to_projection": (
        "relay_teams.sessions.session_rounds_projection",
        "approvals_to_projection",
    ),
    "build_session_rounds": (
        "relay_teams.sessions.session_rounds_projection",
        "build_session_rounds",
    ),
    "find_round_by_run_id": (
        "relay_teams.sessions.session_rounds_projection",
        "find_round_by_run_id",
    ),
    "paginate_rounds": (
        "relay_teams.sessions.session_rounds_projection",
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
