# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import PurePosixPath
import re

from relay_teams.memory.skill_draft_models import (
    MemorySkillDraft,
    MemorySkillDraftValidationMessage,
    MemorySkillDraftValidationSeverity,
)

_RUNTIME_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_BANNED_DOC_NAMES = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "INSTALLATION_GUIDE.md",
        "QUICK_REFERENCE.md",
    }
)
_MAX_INSTRUCTIONS_CHARS = 20000


class SkillDraftValidator:
    def validate(self, draft: MemorySkillDraft) -> MemorySkillDraft:
        messages: list[MemorySkillDraftValidationMessage] = []
        self._validate_runtime_name(draft, messages)
        self._validate_description(draft, messages)
        self._validate_instructions(draft, messages)
        self._validate_files(draft, messages)
        return draft.model_copy(update={"validation_messages": tuple(messages)})

    @staticmethod
    def has_errors(draft: MemorySkillDraft) -> bool:
        return any(
            message.severity == MemorySkillDraftValidationSeverity.ERROR
            for message in draft.validation_messages
        )

    @staticmethod
    def _validate_runtime_name(
        draft: MemorySkillDraft,
        messages: list[MemorySkillDraftValidationMessage],
    ) -> None:
        if not _RUNTIME_NAME_PATTERN.fullmatch(draft.runtime_name.strip()):
            messages.append(
                MemorySkillDraftValidationMessage(
                    severity=MemorySkillDraftValidationSeverity.ERROR,
                    code="invalid_runtime_name",
                    message=(
                        "runtime_name must use lowercase letters, digits, and "
                        "hyphens, start with a letter or digit, and be at most "
                        "64 characters"
                    ),
                    path="SKILL.md",
                )
            )

    @staticmethod
    def _validate_description(
        draft: MemorySkillDraft,
        messages: list[MemorySkillDraftValidationMessage],
    ) -> None:
        if not draft.description.strip():
            messages.append(
                MemorySkillDraftValidationMessage(
                    severity=MemorySkillDraftValidationSeverity.ERROR,
                    code="missing_description",
                    message="description is required in SKILL.md front matter",
                    path="SKILL.md",
                )
            )
        if "\n" in draft.description:
            messages.append(
                MemorySkillDraftValidationMessage(
                    severity=MemorySkillDraftValidationSeverity.ERROR,
                    code="multiline_description",
                    message="description must be a single front matter string",
                    path="SKILL.md",
                )
            )

    @staticmethod
    def _validate_instructions(
        draft: MemorySkillDraft,
        messages: list[MemorySkillDraftValidationMessage],
    ) -> None:
        instructions = draft.instructions.strip()
        if not instructions:
            messages.append(
                MemorySkillDraftValidationMessage(
                    severity=MemorySkillDraftValidationSeverity.ERROR,
                    code="missing_instructions",
                    message="SKILL.md body instructions are required",
                    path="SKILL.md",
                )
            )
            return
        if len(instructions) > _MAX_INSTRUCTIONS_CHARS:
            messages.append(
                MemorySkillDraftValidationMessage(
                    severity=MemorySkillDraftValidationSeverity.WARNING,
                    code="long_instructions",
                    message="instructions are long; move detailed references into files",
                    path="SKILL.md",
                )
            )

    @staticmethod
    def _validate_files(
        draft: MemorySkillDraft,
        messages: list[MemorySkillDraftValidationMessage],
    ) -> None:
        seen_paths: set[str] = set()
        for file in draft.files:
            path_text = file.path.strip()
            path = PurePosixPath(path_text)
            if (
                not path_text
                or path.is_absolute()
                or ".." in path.parts
                or path_text.startswith(".")
            ):
                messages.append(
                    MemorySkillDraftValidationMessage(
                        severity=MemorySkillDraftValidationSeverity.ERROR,
                        code="invalid_file_path",
                        message="file paths must be relative and stay inside the skill",
                        path=path_text,
                    )
                )
                continue
            if path_text == "SKILL.md":
                messages.append(
                    MemorySkillDraftValidationMessage(
                        severity=MemorySkillDraftValidationSeverity.ERROR,
                        code="skill_md_managed",
                        message="SKILL.md is generated from structured fields",
                        path=path_text,
                    )
                )
            if path.name in _BANNED_DOC_NAMES:
                messages.append(
                    MemorySkillDraftValidationMessage(
                        severity=MemorySkillDraftValidationSeverity.ERROR,
                        code="extraneous_doc",
                        message="skills must not include auxiliary documentation files",
                        path=path_text,
                    )
                )
            if path_text in seen_paths:
                messages.append(
                    MemorySkillDraftValidationMessage(
                        severity=MemorySkillDraftValidationSeverity.ERROR,
                        code="duplicate_file_path",
                        message="duplicate skill file path",
                        path=path_text,
                    )
                )
            seen_paths.add(path_text)
            if file.encoding not in {"utf-8", "base64"}:
                messages.append(
                    MemorySkillDraftValidationMessage(
                        severity=MemorySkillDraftValidationSeverity.ERROR,
                        code="invalid_file_encoding",
                        message="file encoding must be utf-8 or base64",
                        path=path_text,
                    )
                )
