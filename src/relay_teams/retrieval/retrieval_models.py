# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RetrievalScopeKind(str, Enum):
    SKILL = "skill"
    MEMORY = "memory"
    MCP = "mcp"
    FILE = "file"
    TODO = "todo"
    CUSTOM = "custom"


class RetrievalBackendKind(str, Enum):
    SQLITE_FTS5 = "sqlite_fts5"


class RetrievalTokenizer(str, Enum):
    UNICODE61 = "unicode61"
    TRIGRAM = "trigram"


class RetrievalDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: RetrievalScopeKind
    scope_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    title: str = ""
    body: str = ""
    keywords: tuple[str, ...] = ()


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: RetrievalScopeKind
    scope_id: str = Field(min_length=1)
    text: str = ""
    limit: int = Field(default=10, ge=1, le=100)


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=1)
    title: str = ""
    snippet: str = ""


class RetrievalScopeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: RetrievalScopeKind
    scope_id: str = Field(min_length=1)
    backend: RetrievalBackendKind = RetrievalBackendKind.SQLITE_FTS5
    tokenizer: RetrievalTokenizer = RetrievalTokenizer.UNICODE61
    title_weight: float = Field(default=5.0, ge=0.0)
    body_weight: float = Field(default=1.0, ge=0.0)
    keyword_weight: float = Field(default=3.0, ge=0.0)


class RetrievalStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: RetrievalScopeKind
    scope_id: str = Field(min_length=1)
    backend: RetrievalBackendKind
    tokenizer: RetrievalTokenizer | None = None
    document_count: int = Field(default=0, ge=0)
    updated_at: datetime | None = None
