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


class RipgrepNotFoundError(RipgrepError):
    """ripgrep 不可用"""
