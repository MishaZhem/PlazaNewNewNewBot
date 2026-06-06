"""Configuration loaded from environment variables via python-dotenv."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


@dataclass
class _Config:
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    PLAZA_USERNAME: str
    PLAZA_PASSWORD: str
    PLAZA_CLIENT_ID: str
    PLAZA_SESSION_COOKIE: str
    TARGET_CITY: str
    POLL_INTERVAL_SECONDS: int
    DRY_RUN: bool
    # -- Anti-detection / pacing --
    POLL_INTERVAL_MIN_SECONDS: int
    POLL_INTERVAL_MAX_SECONDS: int
    ACTIVE_HOURS_START: int
    ACTIVE_HOURS_END: int
    APPLY_DELAY_MIN_SECONDS: int
    APPLY_DELAY_MAX_SECONDS: int
    RATE_LIMIT_COOLDOWN_SECONDS: int
    SESSION_FILE: str


Config = _Config(
    TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    TELEGRAM_CHAT_ID=os.environ.get("TELEGRAM_CHAT_ID", ""),
    PLAZA_USERNAME=os.environ.get("PLAZA_USERNAME", ""),
    PLAZA_PASSWORD=os.environ.get("PLAZA_PASSWORD", ""),
    PLAZA_CLIENT_ID=os.environ.get("PLAZA_CLIENT_ID", "wzp"),
    PLAZA_SESSION_COOKIE=os.environ.get("PLAZA_SESSION_COOKIE", ""),
    TARGET_CITY=os.environ.get("TARGET_CITY", "Delft"),
    POLL_INTERVAL_SECONDS=int(os.environ.get("POLL_INTERVAL_SECONDS", "60")),
    DRY_RUN=_parse_bool(os.environ.get("DRY_RUN", "false")),
    # -- Anti-detection / pacing --
    POLL_INTERVAL_MIN_SECONDS=int(os.environ.get("POLL_INTERVAL_MIN_SECONDS", "45")),
    POLL_INTERVAL_MAX_SECONDS=int(os.environ.get("POLL_INTERVAL_MAX_SECONDS", "90")),
    ACTIVE_HOURS_START=int(os.environ.get("ACTIVE_HOURS_START", "0")),
    ACTIVE_HOURS_END=int(os.environ.get("ACTIVE_HOURS_END", "24")),
    APPLY_DELAY_MIN_SECONDS=int(os.environ.get("APPLY_DELAY_MIN_SECONDS", "10")),
    APPLY_DELAY_MAX_SECONDS=int(os.environ.get("APPLY_DELAY_MAX_SECONDS", "60")),
    RATE_LIMIT_COOLDOWN_SECONDS=int(os.environ.get("RATE_LIMIT_COOLDOWN_SECONDS", "1800")),
    SESSION_FILE=os.environ.get(
        "SESSION_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json"),
    ),
)
