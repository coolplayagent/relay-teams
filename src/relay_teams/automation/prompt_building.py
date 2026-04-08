# -*- coding: utf-8 -*-
from __future__ import annotations


def build_automation_prompt(*, project_name: str, prompt: str) -> str:
    normalized_name = str(project_name).strip()
    normalized_prompt = str(prompt).strip()
    return f"触发定时任务 “{normalized_name}”：\n{normalized_prompt}"


__all__ = ["build_automation_prompt"]
