# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ComplianceCheckCategory(str, Enum):
    """Compliance check categories aligned with AGENTS.md coding standards."""

    MODEL_TYPES = "model_types"
    ANNOTATIONS = "annotations"
    IMPORTS = "imports"
    PATH_USAGE = "path_usage"
    EMOJI_FREE = "emoji_free"
    TYPE_IGNORE_FREE = "type_ignore_free"
    HASATTR_FREE = "hasattr_free"
    MODULE_INIT = "module_init"


class ComplianceCheckResult(BaseModel):
    """Result of a single compliance check."""

    category: ComplianceCheckCategory
    passed: bool
    violations: tuple[str, ...] = ()


class ComplianceRunResult(BaseModel):
    """Aggregate compliance run result."""

    timestamp: datetime
    modules_checked: int
    checks: tuple[ComplianceCheckResult, ...]
    overall_score: float
    violations_by_file: dict[str, tuple[ComplianceCheckResult, ...]]
