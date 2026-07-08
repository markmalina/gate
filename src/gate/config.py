"""Configuration loading for Gate.

This module reads configuration from environment variables only.
Secrets must not be committed to the repository.
"""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class GateConfig:
    telegram_bot_token: str | None
    telegram_bot_username: str
    environment: str
    default_mode: str
    database_url: str | None
    openai_api_key: str | None
    mcore_repo: str | None
    mcore_branch: str



def load_config() -> GateConfig:
    return GateConfig(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", "gate_m_bot"),
        environment=os.getenv("GATE_ENV", "development"),
        default_mode=os.getenv("GATE_DEFAULT_MODE", "manual"),
        database_url=os.getenv("DATABASE_URL"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        mcore_repo=os.getenv("MCORE_REPO"),
        mcore_branch=os.getenv("MCORE_BRANCH", "main"),
    )
