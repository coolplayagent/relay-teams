# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum

import typer

type RequestJsonCallable = Callable[
    [str, str, str, dict[str, object] | None], dict[str, object] | list[object]
]
type AutoStartCallable = Callable[[str, bool], None]


class QuestionsOutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


def build_questions_app(
    *,
    request_json: RequestJsonCallable,
    auto_start_if_needed: AutoStartCallable,
    default_base_url: str,
) -> typer.Typer:
    questions_app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)

    @questions_app.command("list")
    def list_questions(
        run_id: str = typer.Option(..., "--run-id"),
        output_format: QuestionsOutputFormat = typer.Option(
            QuestionsOutputFormat.TABLE,
            "--format",
            help="Output format: table or json.",
            case_sensitive=False,
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        result = request_json(base_url, "GET", f"/api/runs/{run_id}/questions", None)
        items = _extract_question_items(result)
        if output_format == QuestionsOutputFormat.JSON:
            typer.echo(json.dumps(items, ensure_ascii=False))
            return
        typer.echo(_render_questions_table(items))

    @questions_app.command("answer")
    def answer_question(
        run_id: str = typer.Option(..., "--run-id"),
        question_id: str = typer.Option(..., "--question-id"),
        answers_json: str = typer.Option(
            ...,
            "--answers-json",
            help=(
                "JSON array like "
                '\'[{"selections":[{"label":"Yes"}]},{"selections":[{"label":"__none_of_the_above__","supplement":"Need more time"}]}]\''
            ),
        ),
        base_url: str = typer.Option(default_base_url, "--base-url"),
        autostart: bool = typer.Option(True, "--autostart/--no-autostart"),
    ) -> None:
        auto_start_if_needed(base_url, autostart)
        try:
            decoded = json.loads(answers_json)
        except ValueError as exc:
            raise typer.BadParameter("answers-json must be valid JSON") from exc
        if not isinstance(decoded, list):
            raise typer.BadParameter("answers-json must decode to a JSON array")
        result = request_json(
            base_url,
            "POST",
            f"/api/runs/{run_id}/questions/{question_id}:answer",
            {"answers": decoded},
        )
        typer.echo(json.dumps(result, ensure_ascii=False))

    return questions_app


def _extract_question_items(
    payload: dict[str, object] | list[object],
) -> list[dict[str, object]]:
    items = payload
    if isinstance(payload, dict):
        data = payload.get("data")
        items = data if isinstance(data, list) else []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _render_questions_table(items: list[dict[str, object]]) -> str:
    if not items:
        return "No user questions found."

    rows = [
        "Question ID | Status | Role | Instance | Prompts | Preview",
        "----------------------------------------------------------",
    ]
    for item in items:
        questions = item.get("questions")
        prompts = questions if isinstance(questions, list) else []
        preview = _preview_question(prompts)
        rows.append(
            " | ".join(
                [
                    str(item.get("question_id") or ""),
                    str(item.get("status") or ""),
                    str(item.get("role_id") or ""),
                    str(item.get("instance_id") or ""),
                    str(len(prompts)),
                    preview,
                ]
            )
        )
    return "\n".join(rows)


def _preview_question(prompts: list[object]) -> str:
    if not prompts:
        return "-"
    first = prompts[0]
    if not isinstance(first, dict):
        return "-"
    question = str(first.get("question") or "").strip()
    return question or "-"
