from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    cascade: bool = False
    reason: str | None = Field(default=None, min_length=1)
