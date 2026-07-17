from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    workspace: Path
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    agent_timeout_seconds: int
    claude_cli_path: str
    codex_cli_path: str


def get_settings() -> Settings:
    load_env()
    workspace = Path(os.getenv("AGENTHUB_WORKSPACE", PROJECT_ROOT)).expanduser().resolve()
    timeout_raw = os.getenv("AGENTHUB_AGENT_TIMEOUT_SECONDS", "300")
    try:
        timeout = max(10, int(timeout_raw))
    except ValueError:
        timeout = 300
    return Settings(
        workspace=workspace,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        agent_timeout_seconds=timeout,
        claude_cli_path=os.getenv("CLAUDE_CLI_PATH", ""),
        codex_cli_path=os.getenv("CODEX_CLI_PATH", ""),
    )
