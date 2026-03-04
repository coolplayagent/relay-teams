from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrationEnvironment:
    api_base_url: str
    fake_llm_admin_url: str
    fake_llm_v1_base_url: str
    config_dir: Path
    backend_log_file: Path
    fake_llm_log_file: Path
