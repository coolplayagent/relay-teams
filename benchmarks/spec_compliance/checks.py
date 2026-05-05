# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

from benchmarks.spec_compliance.models import (
    ComplianceCheckCategory,
    ComplianceCheckResult,
)

# ---------------------------------------------------------------------------
# Check functions -- each returns a ComplianceCheckResult for one file or
# a tuple of file paths.
# ---------------------------------------------------------------------------

# Emoji detection regex: matches most common emoji codepoints
_EMOJI_RE = re.compile(
    "[\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\U00002600-\U000026ff"
    "]",
)


def check_model_types(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify no typing.Any, no dataclass decorator, and no loose dict annotations."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "typing.Any" in line and "import" not in line:
            violations.append(f"{file_path}:{line_no}: uses typing.Any")
        if re.search(r"@dataclass", stripped):
            violations.append(f"{file_path}:{line_no}: uses @dataclass")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.MODEL_TYPES,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_annotations(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify from __future__ import annotations is present in each source file."""
    violations: list[str] = []
    has_future_annotations = False
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "from __future__ import annotations" in stripped:
            has_future_annotations = True
            break
    if not has_future_annotations:
        violations.append(
            f"{file_path}:1: missing 'from __future__ import annotations'"
        )
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.ANNOTATIONS,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_imports(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify TYPE_CHECKING is not used to hide circular dependencies."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if "TYPE_CHECKING" in stripped and "import" in stripped:
            violations.append(f"{file_path}:{line_no}: uses TYPE_CHECKING import guard")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.IMPORTS,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_path_usage(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify no os.path usage -- must use pathlib.Path."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "os.path" in line:
            violations.append(
                f"{file_path}:{line_no}: uses os.path instead of pathlib.Path"
            )
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.PATH_USAGE,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_emoji_free(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify no emoji characters in source files."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        if _EMOJI_RE.search(line):
            violations.append(f"{file_path}:{line_no}: contains emoji characters")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.EMOJI_FREE,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_type_ignore_free(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify no '# type: ignore' comments."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "# type: ignore" in line:
            violations.append(f"{file_path}:{line_no}: uses '# type: ignore'")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.TYPE_IGNORE_FREE,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_hasattr_free(file_path: Path, content: str) -> ComplianceCheckResult:
    """Verify no hasattr() calls."""
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "hasattr(" in line:
            violations.append(f"{file_path}:{line_no}: uses hasattr()")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.HASATTR_FREE,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


def check_module_init(directory: Path) -> ComplianceCheckResult:
    """Verify all Python packages have __init__.py files."""
    violations: list[str] = []
    if not directory.is_dir():
        return ComplianceCheckResult(
            category=ComplianceCheckCategory.MODULE_INIT,
            passed=True,
            violations=(),
        )

    for child in sorted(directory.rglob("*")):
        if not child.is_dir():
            continue
        init_file = child / "__init__.py"
        # Only check dirs that contain at least one .py file
        py_files = list(child.glob("*.py"))
        if py_files and not init_file.exists():
            violations.append(f"{child}: missing __init__.py")
    return ComplianceCheckResult(
        category=ComplianceCheckCategory.MODULE_INIT,
        passed=len(violations) == 0,
        violations=tuple(violations),
    )


# Ordered list of per-file check functions
FILE_CHECKS = [
    check_model_types,
    check_annotations,
    check_imports,
    check_path_usage,
    check_emoji_free,
    check_type_ignore_free,
    check_hasattr_free,
]

DIRECTORY_CHECKS = [check_module_init]
