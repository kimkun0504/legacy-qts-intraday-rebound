"""Credentials from the repository's ``.env`` (gitignored) or the environment. Never printed."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

S3_ACCESS = "MASSIVE_S3_ACCESS_KEY_ID"
S3_SECRET = "MASSIVE_S3_SECRET_ACCESS_KEY"
API_KEY = "MASSIVE_API_KEY"

PLACEHOLDER = "paste-"


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """``KEY=VALUE`` lines; ``#`` comments, blank lines, ``export`` and quotes are tolerated."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def get(name: str, path: Path = ENV_FILE) -> str | None:
    """Environment variable first, then ``.env``; placeholders and empty values count as missing."""
    v = os.environ.get(name) or read_env_file(path).get(name)
    if not v or v.startswith(PLACEHOLDER):
        return None
    return v.strip()


class MissingCredential(RuntimeError):
    pass


def require(name: str, path: Path = ENV_FILE) -> str:
    v = get(name, path)
    if v is None:
        raise MissingCredential(
            f"{name} is not set. Put it in {path} (copy .env.example to .env, paste the key from "
            "the Massive dashboard after the '=' sign, save)."
        )
    return v
