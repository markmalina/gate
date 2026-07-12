"""Runtime configuration for Gate.

Configuration is loaded from environment variables. Secrets are referenced by variable
name only and must never be committed.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field(
        default="gate_m_bot", validation_alias="TELEGRAM_BOT_USERNAME"
    )
    gate_env: str = Field(default="development", validation_alias="GATE_ENV")
    gate_default_mode: str = Field(default="manual", validation_alias="GATE_DEFAULT_MODE")
    action_bearer_token: str = Field(default="", validation_alias="ACTION_BEARER_TOKEN")
    admin_bearer_token: str = Field(default="", validation_alias="ADMIN_BEARER_TOKEN")
    gate_mcp_enabled: bool = Field(default=False, validation_alias="GATE_MCP_ENABLED")
    mcp_auth_mode: str = Field(default="temporary_bearer", validation_alias="MCP_AUTH_MODE")
    mcp_authorization_server: str = Field(default="", validation_alias="MCP_AUTHORIZATION_SERVER")
    mcp_public_resource_url: str = Field(default="", validation_alias="MCP_PUBLIC_RESOURCE_URL")
    mcp_read_bearer_token: str = Field(default="", validation_alias="MCP_READ_BEARER_TOKEN")
    mcp_read_workspace_id: str = Field(default="", validation_alias="MCP_READ_WORKSPACE_ID")
    mcp_read_account_ids: str = Field(default="", validation_alias="MCP_READ_ACCOUNT_IDS")
    mcp_read_project_ids: str = Field(default="", validation_alias="MCP_READ_PROJECT_IDS")
    deployment_sha: str = Field(default="unknown", validation_alias="RAILWAY_GIT_COMMIT_SHA")
    deployment_service: str = Field(default="gate", validation_alias="RAILWAY_SERVICE_NAME")
    gate_account_ru_business_connection_id: str = Field(
        default="", validation_alias="GATE_ACCOUNT_RU_BUSINESS_CONNECTION_ID"
    )
    gate_account_by_business_connection_id: str = Field(
        default="", validation_alias="GATE_ACCOUNT_BY_BUSINESS_CONNECTION_ID"
    )
    gate_send_enabled: bool = Field(default=True, validation_alias="GATE_SEND_ENABLED")
    gate_account_ru_send_enabled: bool = Field(
        default=True, validation_alias="GATE_ACCOUNT_RU_SEND_ENABLED"
    )
    gate_account_by_send_enabled: bool = Field(
        default=True, validation_alias="GATE_ACCOUNT_BY_SEND_ENABLED"
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""

    return Settings()
