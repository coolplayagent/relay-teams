# -*- coding: utf-8 -*-
from __future__ import annotations

import re


_NON_ID_CHARS = re.compile(r"[^a-zA-Z0-9]+")


def build_workspace_id(session_id: str) -> str:
    return f"ws_{_normalize_id_part(session_id)}"


def build_conversation_id(session_id: str, role_id: str) -> str:
    normalized_session = _normalize_id_part(session_id)
    normalized_role = _normalize_id_part(role_id)
    return f"conv_{normalized_session}_{normalized_role}"


def build_instance_workspace_id(
    session_id: str,
    role_id: str,
    instance_id: str,
) -> str:
    normalized_session = _normalize_id_part(session_id)
    normalized_role = _normalize_id_part(role_id)
    normalized_instance = _normalize_id_part(instance_id)
    return f"ws_{normalized_session}_{normalized_role}_{normalized_instance}"


def build_instance_conversation_id(
    session_id: str,
    role_id: str,
    instance_id: str,
) -> str:
    normalized_session = _normalize_id_part(session_id)
    normalized_role = _normalize_id_part(role_id)
    normalized_instance = _normalize_id_part(instance_id)
    return f"conv_{normalized_session}_{normalized_role}_{normalized_instance}"


def build_instance_session_scope_id(session_id: str, instance_id: str) -> str:
    normalized_session = _normalize_id_part(session_id)
    normalized_instance = _normalize_id_part(instance_id)
    return f"session_{normalized_session}_{normalized_instance}"


def build_instance_role_scope_id(
    session_id: str,
    role_id: str,
    instance_id: str,
) -> str:
    normalized_session = _normalize_id_part(session_id)
    normalized_role = _normalize_id_part(role_id)
    normalized_instance = _normalize_id_part(instance_id)
    return f"role_{normalized_session}_{normalized_role}_{normalized_instance}"


def _normalize_id_part(value: str) -> str:
    normalized = _NON_ID_CHARS.sub("_", value).strip("_").lower()
    return normalized or "default"
