# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.validation import RequiredIdentifierStr

NONE_OF_THE_ABOVE_OPTION_LABEL = "__none_of_the_above__"


class UserQuestionRequestStatus(str, Enum):
    REQUESTED = "requested"
    ANSWERED = "answered"
    TIMED_OUT = "timed_out"
    COMPLETED = "completed"


class UserQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    description: str = ""

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Option label must not be empty")
        return normalized


def _option_label(value: object) -> str:
    if isinstance(value, UserQuestionOption):
        return value.label
    if isinstance(value, dict):
        label = value.get("label")
        return str(label).strip() if isinstance(label, str) else ""
    return ""


def _option_description(value: object) -> str:
    if isinstance(value, UserQuestionOption):
        return value.description
    if isinstance(value, dict):
        description = value.get("description")
        return str(description).strip() if isinstance(description, str) else ""
    return ""


class UserQuestionPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    header: str = ""
    question: str = Field(min_length=1)
    options: tuple[UserQuestionOption, ...] = ()
    multiple: bool = False
    placeholder: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_prompt(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.pop("custom", None)
        raw_options = normalized.get("options")
        if not isinstance(raw_options, (list, tuple)):
            raise ValueError(
                "Question options must include at least one explicit option"
            )
        options = list(raw_options)
        explicit_options = [
            option
            for option in options
            if _option_label(option) != NONE_OF_THE_ABOVE_OPTION_LABEL
        ]
        if not explicit_options:
            raise ValueError(
                "Question options must include at least one explicit option"
            )
        reserved_options = [
            option
            for option in options
            if _option_label(option) == NONE_OF_THE_ABOVE_OPTION_LABEL
        ]
        if reserved_options:
            if (
                len(reserved_options) == 1
                and _option_label(options[-1]) == NONE_OF_THE_ABOVE_OPTION_LABEL
                and not _option_description(reserved_options[0])
            ):
                normalized["options"] = tuple(options)
                return normalized
            raise ValueError(
                f"Option label {NONE_OF_THE_ABOVE_OPTION_LABEL} is reserved"
            )
        options.append({"label": NONE_OF_THE_ABOVE_OPTION_LABEL})
        normalized["options"] = tuple(options)
        return normalized

    @model_validator(mode="after")
    def validate_prompt(self) -> UserQuestionPrompt:
        labels = [option.label for option in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError("Question options must not contain duplicate labels")
        return self


class UserQuestionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    supplement: str | None = None

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Selection label must not be empty")
        return normalized

    @field_validator("supplement")
    @classmethod
    def _normalize_supplement(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class UserQuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selections: tuple[UserQuestionSelection, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_answer(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "selections" in value:
            return value
        selected_options = value.get("selected_options")
        free_text = str(value.get("free_text") or "").strip()
        selections: list[dict[str, str]] = []
        if isinstance(selected_options, list):
            for item in selected_options:
                if isinstance(item, str) and item.strip():
                    selections.append({"label": item.strip()})
        if free_text:
            selections.append(
                {
                    "label": NONE_OF_THE_ABOVE_OPTION_LABEL,
                    "supplement": free_text,
                }
            )
        return {"selections": selections}

    @model_validator(mode="after")
    def validate_answer(self) -> UserQuestionAnswer:
        labels = [selection.label for selection in self.selections]
        if len(set(labels)) != len(labels):
            raise ValueError("Answer selections must not contain duplicate labels")
        return self


class UserQuestionAnswerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answers: tuple[UserQuestionAnswer, ...] = Field(min_length=1)


class UserQuestionRequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    task_id: RequiredIdentifierStr
    instance_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    tool_name: RequiredIdentifierStr = "ask_question"
    questions: tuple[UserQuestionPrompt, ...] = Field(min_length=1)
    status: UserQuestionRequestStatus = UserQuestionRequestStatus.REQUESTED
    answers: tuple[UserQuestionAnswer, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    resolved_at: datetime | None = None


class PendingUserQuestionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: RequiredIdentifierStr
    role_id: str = ""
    instance_id: str = ""
    requested_at: str = ""
    status: str = "requested"
    questions: tuple[UserQuestionPrompt, ...] = ()
