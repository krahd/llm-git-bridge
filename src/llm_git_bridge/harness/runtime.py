"""Runtime paths for the standalone conversation harness."""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "chatgpt-conversation-harness-v1"
LAUNCH_AGENT_LABEL = "net.laurenzo.chatgpt-conversation-harness-v1"


def state_dir() -> Path:
    override = os.environ.get("LLM_HARNESS_STATE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".local" / "state" / APP_ID


def database_path() -> Path:
    return state_dir() / "harness.sqlite3"


def socket_path() -> Path:
    return state_dir() / "harness.sock"
