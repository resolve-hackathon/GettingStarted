"""Small .env reader, with real environment variables taking precedence."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().replace("_", "").isalnum():
            raise ValueError("Invalid .env line; use KEY=value without shell syntax")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def data_dir() -> Path:
    return Path(os.getenv("RESOLVE_DATA_DIR", str(ROOT / "data"))).resolve()


def runtime_dir() -> Path:
    return Path(os.getenv("RESOLVE_RUNTIME_DIR", str(ROOT / "runtime"))).resolve()
