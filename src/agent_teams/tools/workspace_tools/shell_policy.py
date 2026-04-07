from __future__ import annotations
import re
import shlex
from enum import Enum
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.sessions.runs.background_tasks.command_runtime import (
    CommandRuntimeKind,
    ResolvedCommandRuntime,
    resolve_command_runtime,
)
from agent_teams.tools.workspace_tools.command_canonicalization import (
    canonicalize_shell_command,
)

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 1200

MAX_COMMAND_LENGTH = 16_000

_BASH_BANNED_COMMANDS = frozenset(
    {
        "alias",
        "aria2c",
        "axel",
        "chrome",
        "chrome.exe",
        "curl",
        "curlie",
        "firefox",
        "firefox.exe",
        "http-prompt",
        "httpie",
        "links",
        "lynx",
        "nc",
        "safari",
        "safari.exe",
        "telnet",
        "w3m",
        "wget",
        "xh",
    }
)
_POWERSHELL_BANNED_COMMANDS = frozenset(
    {
        "chrome",
        "chrome.exe",
        "curl",
        "curl.exe",
        "firefox",
        "firefox.exe",
        "invoke-restmethod",
        "invoke-webrequest",
        "irm",
        "iwr",
        "msedge",
        "msedge.exe",
        "safari",
        "safari.exe",
        "start-bitstransfer",
        "wget",
        "wget.exe",
    }
)
_SPECIAL_SUBCOMMAND_PREFIXES = frozenset({"gh", "git", "npm", "pnpm", "yarn"})
_SIMPLE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_./:\\-]+$")
_ENV_ASSIGNMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_CMDLET_PATTERN = re.compile(r"^[A-Za-z]+-[A-Za-z][A-Za-z0-9-]*$")
_POWERSHELL_DIRECTORY_COMMANDS = frozenset(
    {"cd", "chdir", "push-location", "pushd", "set-location", "sl"}
)
_BASH_DIRECTORY_COMMANDS = frozenset({"cd", "pushd"})
_BASH_COMMAND_WRAPPERS = frozenset({"builtin", "command", "env", "noglob"})
_BASH_DYNAMIC_DIRECTORY_PATTERNS = ("$", "~", "`", "*", "?", "[", "]", "{", "}")
_POWERSHELL_DYNAMIC_DIRECTORY_PATTERNS = ("$", "`", "*", "?", "[", "]", "{", "}")


class ShellRuntimeFamily(str, Enum):
    BASH = "bash"
    GIT_BASH = "git-bash"
    POWERSHELL = "powershell"


class ShellPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_family: ShellRuntimeFamily
    normalized_command: str = Field(min_length=1)
    subcommands: tuple[str, ...] = Field(default_factory=tuple)
    prefix_candidates: tuple[str, ...] = Field(default_factory=tuple)


def normalize_timeout(timeout_seconds: int | None) -> int:
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        return MAX_TIMEOUT_SECONDS
    return timeout_seconds


def validate_shell_command(
    command: str,
    *,
    yolo: bool = False,
    effective_cwd: Path | None = None,
) -> ShellPolicyDecision:
    normalized_command = canonicalize_shell_command(command).strip()
    if not normalized_command:
        raise ValueError("command must not be empty")
    if len(normalized_command) > MAX_COMMAND_LENGTH:
        raise ValueError(
            "command is too long "
            f"({len(normalized_command)} chars, max {MAX_COMMAND_LENGTH})"
        )
    runtime = resolve_command_runtime(command=normalized_command)
    runtime_family = _runtime_family(runtime)
    subcommands = _split_subcommands(
        normalized_command,
        runtime_family=runtime_family,
    )
    if not subcommands:
        raise ValueError("command must not be empty")
    prefixes: list[str] = []
    normalized_subcommands: list[str] = []
    for index, subcommand in enumerate(subcommands):
        if (
            yolo
            and effective_cwd is not None
            and _validate_directory_change(
                subcommand=subcommand,
                runtime_family=runtime_family,
                effective_cwd=effective_cwd,
            )
            and index == 0
            and len(subcommands) > 1
        ):
            continue
        blocked_name = _blocked_command_name(
            subcommand=subcommand,
            runtime_family=runtime_family,
        )
        if blocked_name is not None:
            raise ValueError(
                f"command is blocked by local shell policy: {blocked_name}"
            )
        prefixes.append(
            _build_prefix_candidate(
                subcommand=subcommand,
                runtime_family=runtime_family,
            )
        )
        normalized_subcommands.append(subcommand)
    if not normalized_subcommands:
        normalized_subcommands = subcommands
    return ShellPolicyDecision(
        runtime_family=runtime_family,
        normalized_command=normalized_command,
        subcommands=tuple(normalized_subcommands),
        prefix_candidates=tuple(prefixes),
    )


def _runtime_family(runtime: ResolvedCommandRuntime) -> ShellRuntimeFamily:
    if runtime.kind == CommandRuntimeKind.POWERSHELL:
        return ShellRuntimeFamily.POWERSHELL
    if runtime.display_name.lower() == "git bash":
        return ShellRuntimeFamily.GIT_BASH
    return ShellRuntimeFamily.BASH


def _split_subcommands(
    command: str,
    *,
    runtime_family: ShellRuntimeFamily,
) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape_next = False
    index = 0
    while index < len(command):
        char = command[index]
        if escape_next:
            current.append(char)
            escape_next = False
            index += 1
            continue
        if char == "\\" and not in_single:
            current.append(char)
            escape_next = True
            index += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            index += 1
            continue
        if in_single or in_double:
            current.append(char)
            index += 1
            continue
        next_char = command[index + 1] if index + 1 < len(command) else ""
        if char == "&" and next_char == "&":
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2
            continue
        if (
            runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}
            and char == "&"
        ):
            prev_char = command[index - 1] if index > 0 else ""
            if prev_char != ">" and next_char != ">":
                segment = "".join(current).strip()
                if segment:
                    segments.append(segment)
                current = []
                index += 1
                continue
        if char == "|" and next_char == "|":
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2
            continue
        if char in {";", "|", "\n"}:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _blocked_command_name(
    *,
    subcommand: str,
    runtime_family: ShellRuntimeFamily,
) -> str | None:
    _command_name, normalized_name, tokens = _extract_command_identity(
        subcommand=subcommand,
        runtime_family=runtime_family,
    )
    if runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}:
        stripped_exe_name = (
            normalized_name.removesuffix(".exe")
            if normalized_name.endswith(".exe")
            else normalized_name
        )
        if (
            normalized_name in _BASH_BANNED_COMMANDS
            or stripped_exe_name in _BASH_BANNED_COMMANDS
        ):
            return normalized_name
        substitution_blocked = _blocked_bash_substitution_command_name(subcommand)
        if substitution_blocked is not None:
            return substitution_blocked
        return None
    if normalized_name in _POWERSHELL_BANNED_COMMANDS:
        return normalized_name
    if normalized_name == "start-process" and _targets_browser(tokens[1:]):
        return "start-process"
    return None


def _validate_directory_change(
    *,
    subcommand: str,
    runtime_family: ShellRuntimeFamily,
    effective_cwd: Path,
) -> bool:
    _command_name, normalized_name, tokens = _extract_command_identity(
        subcommand=subcommand,
        runtime_family=runtime_family,
    )
    if runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}:
        if normalized_name not in _BASH_DIRECTORY_COMMANDS:
            return False
        if _bash_directory_uses_cdpath(subcommand):
            raise ValueError(
                "directory change is blocked by local shell policy: "
                "CDPATH requires shell expansion"
            )
        target = _extract_bash_directory_target(
            subcommand=subcommand,
            command_name=normalized_name,
        )
        if target is None:
            return False
        if not _is_static_directory_target(
            raw_target=target,
            runtime_family=runtime_family,
        ):
            raise ValueError(
                "directory change is blocked by local shell policy: "
                f"{target} requires shell expansion"
            )
    else:
        if normalized_name not in _POWERSHELL_DIRECTORY_COMMANDS:
            return False
        target = _extract_powershell_directory_target(tokens[1:])
        if target is None:
            return False
        if not _is_static_directory_target(
            raw_target=target,
            runtime_family=runtime_family,
        ):
            raise ValueError(
                "directory change is blocked by local shell policy: "
                f"{target} requires shell expansion"
            )
    resolved_cwd = effective_cwd.resolve()
    resolved_target = _resolve_directory_target(
        raw_target=target,
        effective_cwd=resolved_cwd,
    )
    if resolved_target == resolved_cwd:
        return True
    if resolved_cwd not in resolved_target.parents:
        raise ValueError(
            "directory change is blocked by local shell policy: "
            f"{resolved_target} is outside {resolved_cwd}"
        )
    return False


def _build_prefix_candidate(
    *,
    subcommand: str,
    runtime_family: ShellRuntimeFamily,
) -> str:
    _command_name, normalized_name, tokens = _extract_command_identity(
        subcommand=subcommand,
        runtime_family=runtime_family,
    )
    if not tokens:
        return normalized_name
    if normalized_name in _SPECIAL_SUBCOMMAND_PREFIXES:
        subcommand_name = _extract_first_simple_argument(tokens[1:])
        if subcommand_name is not None:
            return f"{normalized_name} {subcommand_name.lower()}"
    return normalized_name


def _extract_command_identity(
    *,
    subcommand: str,
    runtime_family: ShellRuntimeFamily,
) -> tuple[str, str, list[str]]:
    command_name, tokens = _extract_command_tokens(
        subcommand=subcommand,
        runtime_family=runtime_family,
    )
    return command_name, _normalize_command_name(command_name), tokens


def _extract_command_tokens(
    *,
    subcommand: str,
    runtime_family: ShellRuntimeFamily,
) -> tuple[str, list[str]]:
    tokens = _split_command_tokens(subcommand, runtime_family=runtime_family)
    if runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}:
        tokens = _strip_bash_leading_wrappers(tokens)
    if runtime_family == ShellRuntimeFamily.POWERSHELL and tokens and tokens[0] == "&":
        tokens = tokens[1:]
    if not tokens:
        return "", []
    return tokens[0], tokens


def _split_command_tokens(
    command: str,
    *,
    runtime_family: ShellRuntimeFamily,
) -> list[str]:
    if runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}:
        try:
            return shlex.split(command, posix=True)
        except ValueError:
            return _fallback_tokenize(command)
    return _powershell_tokenize(command)


def _strip_bash_env_assignments(tokens: list[str]) -> list[str]:
    index = 0
    while (
        index < len(tokens) and _ENV_ASSIGNMENT_PATTERN.match(tokens[index]) is not None
    ):
        index += 1
    return tokens[index:]


def _strip_bash_leading_wrappers(tokens: list[str]) -> list[str]:
    current = _strip_bash_env_assignments(tokens)
    while current:
        wrapper = _normalize_command_name(current[0])
        if wrapper not in _BASH_COMMAND_WRAPPERS:
            return current
        if wrapper == "env":
            updated = _strip_bash_env_wrapper(current)
        else:
            updated = _strip_bash_command_wrapper(current)
        if updated == current:
            return current
        current = _strip_bash_env_assignments(updated)
    return current


def _strip_bash_env_wrapper(tokens: list[str]) -> list[str]:
    if not tokens or _normalize_command_name(tokens[0]) != "env":
        return tokens
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-") and token != "-":
            index += 1
            continue
        if _ENV_ASSIGNMENT_PATTERN.match(token) is not None:
            index += 1
            continue
        break
    return tokens[index:] or tokens


def _strip_bash_command_wrapper(tokens: list[str]) -> list[str]:
    if not tokens:
        return tokens
    wrapper = _normalize_command_name(tokens[0])
    if wrapper not in {"builtin", "command", "noglob"}:
        return tokens
    index = 1
    if wrapper == "command":
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                index += 1
                break
            if token.startswith("-") and token != "-":
                index += 1
                continue
            break
    return tokens[index:] or tokens


def _powershell_tokenize(command: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape_next = False
    for char in command:
        if escape_next:
            current.append(char)
            escape_next = False
            continue
        if char == "`":
            escape_next = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char.isspace() and not in_single and not in_double:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _fallback_tokenize(command: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in command:
        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _extract_first_simple_argument(tokens: list[str]) -> str | None:
    for token in tokens:
        if token.startswith("-"):
            continue
        if _SIMPLE_IDENTIFIER_PATTERN.fullmatch(token) is None:
            return None
        return _extract_path_basename(token)
    return None


def _normalize_command_name(command_name: str) -> str:
    stripped = command_name.strip().strip("\"'")
    if not stripped:
        return ""
    if _CMDLET_PATTERN.fullmatch(stripped) is not None:
        return stripped.lower()
    basename = _extract_path_basename(stripped)
    return basename.lower() if basename else stripped.lower()


def _extract_path_basename(raw_path: str) -> str:
    basename = Path(raw_path).name
    if basename != raw_path:
        return basename
    return PureWindowsPath(raw_path).name


def _targets_browser(tokens: list[str]) -> bool:
    browser_names = {
        "chrome",
        "chrome.exe",
        "firefox",
        "firefox.exe",
        "msedge",
        "msedge.exe",
        "safari",
        "safari.exe",
    }
    for token in tokens:
        if token.startswith("-"):
            continue
        if Path(token.strip("\"'")).name.lower() in browser_names:
            return True
    return False


def _extract_powershell_directory_target(tokens: list[str]) -> str | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        normalized = token.lower()
        if normalized in {"-path", "-literalpath"}:
            next_index = index + 1
            if next_index < len(tokens):
                return tokens[next_index]
            return None
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def _extract_bash_directory_target(
    *,
    subcommand: str,
    command_name: str,
) -> str | None:
    tokens = _split_bash_tokens_preserving_windows_paths(subcommand)
    tokens = _strip_bash_leading_wrappers(tokens)
    if not tokens or command_name not in _BASH_DIRECTORY_COMMANDS:
        return None
    tokens = tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if command_name == "cd" and token.startswith("-") and token != "-":
            index += 1
            continue
        return token
    if index < len(tokens):
        return tokens[index]
    return None


def _split_bash_tokens_preserving_windows_paths(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return _fallback_tokenize(command)


def _bash_directory_uses_cdpath(subcommand: str) -> bool:
    tokens = _split_bash_tokens_preserving_windows_paths(subcommand)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        assignment_name = _extract_env_assignment_name(token)
        if assignment_name is not None:
            if assignment_name == "CDPATH":
                return True
            index += 1
            continue
        wrapper = _normalize_command_name(token)
        if wrapper == "env":
            index += 1
            while index < len(tokens):
                env_token = tokens[index]
                if env_token == "--":
                    index += 1
                    break
                if env_token.startswith("-") and env_token != "-":
                    index += 1
                    continue
                assignment_name = _extract_env_assignment_name(env_token)
                if assignment_name is None:
                    break
                if assignment_name == "CDPATH":
                    return True
                index += 1
            continue
        if wrapper in {"builtin", "noglob"}:
            index += 1
            continue
        if wrapper == "command":
            index += 1
            while index < len(tokens):
                option = tokens[index]
                if option == "--":
                    index += 1
                    break
                if option.startswith("-") and option != "-":
                    index += 1
                    continue
                break
            continue
        break
    return False


def _is_static_directory_target(
    *,
    raw_target: str,
    runtime_family: ShellRuntimeFamily,
) -> bool:
    stripped = raw_target.strip().strip("\"'")
    if not stripped:
        return True
    if runtime_family in {ShellRuntimeFamily.BASH, ShellRuntimeFamily.GIT_BASH}:
        if stripped in {"-", "~"} or stripped.startswith(("~", "+")):
            return False
        return not any(
            marker in stripped for marker in _BASH_DYNAMIC_DIRECTORY_PATTERNS
        )
    return not any(
        marker in stripped for marker in _POWERSHELL_DYNAMIC_DIRECTORY_PATTERNS
    )


def _resolve_directory_target(*, raw_target: str, effective_cwd: Path) -> Path:
    target = raw_target.strip().strip("\"'")
    if not target:
        return effective_cwd.resolve()
    normalized_target = _normalize_shell_path(target)
    candidate = Path(normalized_target)
    if candidate.is_absolute():
        return candidate.resolve()
    return (effective_cwd / normalized_target).resolve()


def _normalize_shell_path(raw_target: str) -> str:
    match = re.match(r"^/([a-zA-Z])/(.*)", raw_target)
    if match is None:
        return raw_target
    return f"{match.group(1)}:/{match.group(2)}"


def _extract_env_assignment_name(token: str) -> str | None:
    if _ENV_ASSIGNMENT_PATTERN.match(token) is None:
        return None
    return token.split("=", 1)[0]


def _blocked_bash_substitution_command_name(subcommand: str) -> str | None:
    for substitution in _extract_bash_command_substitutions(subcommand):
        for nested in _split_subcommands(
            substitution,
            runtime_family=ShellRuntimeFamily.BASH,
        ):
            blocked_name = _blocked_command_name(
                subcommand=nested,
                runtime_family=ShellRuntimeFamily.BASH,
            )
            if blocked_name is not None:
                return blocked_name
    return None


def _extract_bash_command_substitutions(command: str) -> list[str]:
    substitutions: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if in_single:
            index += 1
            continue
        if char == "`":
            end_index = _find_unescaped_backtick(command, index + 1)
            if end_index == -1:
                break
            substitutions.append(command[index + 1 : end_index])
            index = end_index + 1
            continue
        if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
            content, next_index = _extract_dollar_substitution(command, index + 2)
            if content is None:
                break
            substitutions.append(content)
            index = next_index
            continue
        index += 1
    return substitutions


def _find_unescaped_backtick(command: str, start_index: int) -> int:
    index = start_index
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == "`":
            return index
        index += 1
    return -1


def _extract_dollar_substitution(
    command: str,
    start_index: int,
) -> tuple[str | None, int]:
    depth = 1
    index = start_index
    in_single = False
    in_double = False
    while index < len(command):
        char = command[index]
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if in_single:
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == "$" and not in_double and index + 1 < len(command):
            next_char = command[index + 1]
            if next_char == "(":
                depth += 1
                index += 2
                continue
        if char == "(" and not in_double:
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return command[start_index:index], index + 1
        index += 1
    return None, len(command)
