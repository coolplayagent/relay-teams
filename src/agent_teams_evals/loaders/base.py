from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agent_teams_evals.models import EvalItem


class DatasetLoader(ABC):
    @abstractmethod
    def load(self, path: Path) -> list[EvalItem]: ...
