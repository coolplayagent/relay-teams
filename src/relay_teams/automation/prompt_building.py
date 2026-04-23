# -*- coding: utf-8 -*-
from __future__ import annotations


def build_automation_prompt(*, project_name: str, prompt: str) -> str:
    normalized_name = str(project_name).strip()
    normalized_prompt = str(prompt).strip()
    return (
        f"自动化项目“{normalized_name}”已由系统触发进入本次执行。\n"
        "不要创建、启动或安排新的定时任务；定时调度由后台负责。"
        "请直接完成以下任务：\n"
        f"{normalized_prompt}"
    )


__all__ = ["build_automation_prompt"]
