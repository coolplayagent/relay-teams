# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GrepMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_num: int
    line_text: str

    def format(self, show_filename: bool = True) -> str:
        prefix = f"{self.path}:" if show_filename else ""
        return f"{prefix}Line {self.line_num}: {self.line_text}"


class GrepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[GrepMatch]
    truncated: bool
    total: int

    def format(self) -> str:
        if not self.matches:
            return "No matches found"

        lines = [f"Found {self.total} matches"]
        if self.truncated:
            lines.append(f"(Results truncated: showing first {len(self.matches)})")

        current_file = ""
        for match in self.matches:
            if match.path != current_file:
                current_file = match.path
                lines.append(f"\n{match.path}:")
            lines.append(f"  {match.format(show_filename=False)}")

        return "\n".join(lines)
