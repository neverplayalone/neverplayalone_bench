from __future__ import annotations

import os
from pathlib import Path


def detect_launch(path: Path) -> list[str]:
    if path.is_dir() and (path / "package.json").exists():
        return ["node", "index.js"]
    if path.is_dir() and (path / "main.py").exists():
        return ["python3", "main.py"]
    if path.is_file() and os.access(path, os.X_OK):
        return [str(path)]
    raise FileNotFoundError(f"don't know how to launch agent at {path}")
