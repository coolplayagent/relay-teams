from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def resolve_bash_path() -> str:
    env_path = os.getenv('GIT_BASH_PATH')
    if env_path and Path(env_path).exists():
        return env_path

    which_bash = shutil.which('bash')
    if which_bash:
        return which_bash

    candidates = (
        r'C:\\Program Files\\Git\\bin\\bash.exe',
        r'C:\\Program Files\\Git\\usr\\bin\\bash.exe',
        r'C:\\Program Files (x86)\\Git\\bin\\bash.exe',
    )
    for item in candidates:
        if Path(item).exists():
            return item

    raise FileNotFoundError('Git Bash executable not found; set GIT_BASH_PATH')


def run_git_bash(*, command: str, workdir: Path, timeout_seconds: int) -> tuple[int, str, str, bool]:
    bash = resolve_bash_path()
    try:
        proc = subprocess.run(
            [bash, '-lc', command],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_seconds,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ''
        err = exc.stderr or ''
        return 124, str(out), str(err), True
