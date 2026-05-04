# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta

# ---------------------------------------------------------------------------
# TTL defaults per tier
# ---------------------------------------------------------------------------
WORKING_TTL: timedelta = timedelta(hours=4)
MEDIUM_TERM_TTL: timedelta = timedelta(days=7)
PERSISTENT_TTL: None = None  # no expiry

# ---------------------------------------------------------------------------
# Confidence decay factors (applied once per day)
# ---------------------------------------------------------------------------
WORKING_DECAY_FACTOR: float = 1.0  # no decay -- short lived
MEDIUM_TERM_DECAY_FACTOR: float = 0.98
PERSISTENT_DECAY_FACTOR: float = 0.995

# ---------------------------------------------------------------------------
# Minimum confidence thresholds
# ---------------------------------------------------------------------------
MIN_CONFIDENCE_ACTIVE: float = 0.2  # below this, entry is auto-expired
MIN_CONFIDENCE_CONSOLIDATION: float = 0.3  # skip entries below during consolidation

# ---------------------------------------------------------------------------
# Capacity limits per scope
# ---------------------------------------------------------------------------
MAX_WORKING_PER_RUN: int = 200
MAX_MEDIUM_TERM_PER_SESSION_ROLE: int = 500
MAX_PERSISTENT_PER_WORKSPACE: int = 2000

# ---------------------------------------------------------------------------
# Retrieval defaults for prompt injection
# ---------------------------------------------------------------------------
INJECTION_MIN_CONFIDENCE: float = 0.5
INJECTION_LIMIT: int = 15

# ---------------------------------------------------------------------------
# Memory ID prefix
# ---------------------------------------------------------------------------
MEMORY_ID_PREFIX: str = "mem-"
