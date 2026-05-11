# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BinaryToolId(str, Enum):
    RIPGREP = "rg"
    GITHUB_CLI = "gh"
    CLAWHUB = "clawhub"


class BinaryToolSourceKind(str, Enum):
    GITHUB_RELEASE = "github_release"
    NPM_GLOBAL = "npm_global"


class BinaryToolPathSource(str, Enum):
    MANAGED = "managed"
    SYSTEM = "system"
    NPM_GLOBAL = "npm_global"


class BinaryToolStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    DOWNLOADING = "downloading"
    ERROR = "error"


class BinaryToolDownloadStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BinaryToolItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: BinaryToolId
    display_name: str = Field(min_length=1)
    version: str | None = None
    source_kind: BinaryToolSourceKind
    status: BinaryToolStatus
    path_source: BinaryToolPathSource | None = None
    path: str | None = None
    executable_name: str = Field(min_length=1)
    download_job_id: str | None = None
    error_message: str | None = None


class BinaryToolListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: tuple[BinaryToolItem, ...]


class BinaryToolDownloadJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    tool_id: BinaryToolId
    status: BinaryToolDownloadStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    downloaded_bytes: int = Field(default=0, ge=0)
    total_bytes: int | None = Field(default=None, ge=0)
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    message: str = Field(min_length=1)
    path: str | None = None
    error_message: str | None = None
