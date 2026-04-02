from __future__ import annotations


class GitHubCliError(Exception):
    """Base error for bundled GitHub CLI management."""


class UnsupportedPlatformError(GitHubCliError):
    def __init__(self, platform_key: str) -> None:
        super().__init__(f"Unsupported platform for bundled gh: {platform_key}")


class DownloadFailedError(GitHubCliError):
    def __init__(self, url: str, status: int) -> None:
        self.url = url
        self.status = status
        super().__init__(f"Failed to download gh from {url}: HTTP {status}")


class ExtractionFailedError(GitHubCliError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class GitHubCliNotFoundError(GitHubCliError):
    def __init__(self) -> None:
        super().__init__(
            "GitHub CLI is not available: bundled binary could not be downloaded and"
            " no system gh was found"
        )
