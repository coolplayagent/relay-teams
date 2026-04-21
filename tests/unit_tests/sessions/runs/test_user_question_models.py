# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.sessions.runs.user_question_models import (
    NONE_OF_THE_ABOVE_OPTION_LABEL,
    UserQuestionPrompt,
)


def test_user_question_prompt_requires_options_field() -> None:
    with pytest.raises(
        ValidationError,
        match="Question options must include at least one explicit option",
    ):
        UserQuestionPrompt.model_validate({"question": "Pick one"})


def test_user_question_prompt_rejects_empty_options() -> None:
    with pytest.raises(
        ValidationError,
        match="Question options must include at least one explicit option",
    ):
        UserQuestionPrompt.model_validate(
            {
                "question": "Pick one",
                "options": [],
            }
        )


def test_user_question_prompt_appends_none_of_the_above_to_explicit_options() -> None:
    prompt = UserQuestionPrompt.model_validate(
        {
            "question": "Pick one",
            "options": [{"label": "Only", "description": "Only option"}],
        }
    )

    assert tuple(option.label for option in prompt.options) == (
        "Only",
        NONE_OF_THE_ABOVE_OPTION_LABEL,
    )


def test_user_question_prompt_rejects_duplicate_option_labels() -> None:
    with pytest.raises(
        ValidationError,
        match="Question options must not contain duplicate labels",
    ):
        UserQuestionPrompt.model_validate(
            {
                "question": "Pick one",
                "options": [
                    {"label": "Same", "description": "First"},
                    {"label": "Same", "description": "Second"},
                ],
            }
        )


def test_user_question_prompt_rejects_duplicate_labels_after_normalization() -> None:
    with pytest.raises(
        ValidationError,
        match="Question options must not contain duplicate labels",
    ):
        UserQuestionPrompt.model_validate(
            {
                "question": "Pick one",
                "options": [
                    {"label": "Same", "description": "First"},
                    {"label": " Same ", "description": "Second"},
                ],
            }
        )
