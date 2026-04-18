from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from typing import IO, Protocol, cast

from relay_teams.logger import get_logger
from relay_teams.tools.office_tools.models import (
    OfficeConversionError,
    OfficeConversionResult,
)

_MARKITDOWN_MODULE = "markitdown"
MARKITDOWN_CONVERTER_NAME = "markitdown"
LOGGER = get_logger(__name__)


class _MarkItDownResult(Protocol):
    markdown: str | None
    text_content: str | None


class _MarkItDownConverter(Protocol):
    def convert(self, source: str | Path) -> _MarkItDownResult: ...


def convert_with_markitdown(file_path: Path) -> OfficeConversionResult:
    converter = _load_markitdown_converter()
    try:
        result = converter.convert(file_path)
    except Exception as exc:  # pragma: no cover - external library behavior
        raise OfficeConversionError(
            f"Failed to convert document to markdown: {file_path.name}"
        ) from exc
    markdown = _extract_markdown(result)
    return OfficeConversionResult(
        markdown=markdown,
        converter_name=MARKITDOWN_CONVERTER_NAME,
        source_extension=file_path.suffix.lower(),
    )


def stream_markdown_with_markitdown(
    file_path: Path,
    *,
    on_line: Callable[[str], None],
) -> None:
    _ensure_markitdown_available()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", _MARKITDOWN_MODULE, str(file_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise OfficeConversionError(
            "Document conversion dependencies are unavailable. "
            "Install relay-teams with markitdown office extras."
        ) from exc

    if process.stdout is None or process.stderr is None:
        process.kill()
        raise OfficeConversionError(
            f"Failed to convert document to markdown: {file_path.name}"
        )

    stderr_chunks: list[str] = []
    stderr_reader = threading.Thread(
        target=_read_stderr_stream,
        args=(process.stderr, stderr_chunks),
        daemon=True,
    )
    stderr_reader.start()

    try:
        for line in _iter_normalized_markdown_lines(process.stdout):
            on_line(line)
    finally:
        process.stdout.close()

    return_code = process.wait()
    stderr_reader.join()
    stderr_output = "".join(stderr_chunks).strip()
    if return_code != 0:
        if stderr_output:
            LOGGER.warning(
                "markitdown subprocess failed for %s: %s",
                file_path,
                stderr_output,
            )
        raise OfficeConversionError(
            f"Failed to convert document to markdown: {file_path.name}"
        )


def _load_markitdown_converter() -> _MarkItDownConverter:
    try:
        module = importlib.import_module(_MARKITDOWN_MODULE)
    except ImportError as exc:
        raise OfficeConversionError(
            "Document conversion dependencies are unavailable. "
            "Install relay-teams with markitdown office extras."
        ) from exc
    converter_cls = getattr(module, "MarkItDown", None)
    if not callable(converter_cls):
        raise OfficeConversionError(
            "Document conversion backend is unavailable: markitdown.MarkItDown"
        )
    return cast(_MarkItDownConverter, converter_cls())


def _ensure_markitdown_available() -> None:
    if importlib.util.find_spec(_MARKITDOWN_MODULE) is None:
        raise OfficeConversionError(
            "Document conversion dependencies are unavailable. "
            "Install relay-teams with markitdown office extras."
        )


def _read_stderr_stream(stream: IO[str], chunks: list[str]) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if chunk == "":
                break
            chunks.append(chunk)
    finally:
        stream.close()


def _iter_normalized_markdown_lines(stream: IO[str]) -> Iterator[str]:
    previous_line: str | None = None
    buffered_blank_lines: list[str] = []
    started = False

    for raw_line in stream:
        line = raw_line.rstrip("\r\n")
        if not started:
            line = line.lstrip()
            if line == "":
                continue
            started = True
        if previous_line is None:
            previous_line = line
            continue
        if previous_line.strip() == "":
            buffered_blank_lines.append(previous_line)
        else:
            for buffered_line in buffered_blank_lines:
                yield buffered_line
            buffered_blank_lines.clear()
            yield previous_line
        previous_line = line

    if previous_line is None:
        return
    if previous_line.strip() == "":
        return
    for buffered_line in buffered_blank_lines:
        yield buffered_line
    yield previous_line.rstrip()


def _extract_markdown(result: _MarkItDownResult) -> str:
    markdown = str(result.markdown or "").strip()
    if markdown:
        return markdown
    return str(result.text_content or "").strip()
