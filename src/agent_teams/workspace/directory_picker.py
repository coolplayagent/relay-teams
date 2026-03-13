# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # pragma: no cover - platform dependent
    tk = None
    filedialog = None


def pick_workspace_directory(initial_dir: Path | None = None) -> Path | None:
    if tk is None or filedialog is None:
        raise RuntimeError("Native directory picker is unavailable")

    start_dir = (initial_dir or Path.home()).resolve()

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            initialdir=str(start_dir),
            mustexist=True,
            title="Select project folder",
        )
        root.destroy()
    except tk.TclError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError("Native directory picker is unavailable") from exc

    if not selected:
        return None
    return Path(selected).resolve()
