# -*- coding: utf-8 -*-
from __future__ import annotations


class RipgrepError(Exception):
    """ripgrep 模块基础错误"""


class UnsupportedPlatformError(RipgrepError):
    """不支持的平台"""

    def __init__(self, platform: str):
        self.platform = platform
        super().__init__(f"Unsupported platform: {platform}")


class DownloadFailedError(RipgrepError):
    """下载失败"""

    def __init__(self, url: str, status: int):
        self.url = url
        self.status = status
        super().__init__(f"Download failed: {url} (status: {status})")


class ExtractionFailedError(RipgrepError):
    """解压失败"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Extraction failed: {reason}")


class RipgrepExecutionError(RipgrepError):
    """ripgrep command failed"""

    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"ripgrep exited with code {returncode}: {stderr}"
            if stderr
            else f"ripgrep exited with code {returncode}"
        )


class RipgrepNotFoundError(RipgrepError):
    """ripgrep 不可用"""

    def __init__(self) -> None:
        super().__init__(
            "ripgrep is not available: bundled binary could not be"
            " downloaded and no system rg was found"
        )
