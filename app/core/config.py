"""Runtime configuration for Gate.

Configuration is loaded from environment variables. Secrets are referenced by variable
name only and must never be committed.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.telegram_connectors import (
    TelegramConnectorRegistry,
    build_telegram_connector_registry,
)


class Settings(BaseSettings):
    """Application settings sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str = Field(
        default="gate_m_bot", validation_alias="TELEGRAM_BOT_USERNAME"
    )
    telegram_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="TELEGRAM_WEBHOOK_SECRET",
    )
    telegram_alpha_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_ALPHA_BOT_TOKEN"
    )
    telegram_alpha_bot_username: str = Field(
        default="", validation_alias="TELEGRAM_ALPHA_BOT_USERNAME"
    )
    telegram_alpha_webhook_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_ALPHA_WEBHOOK_SECRET"
    )
    gate_env: str = Field(default="development", validation_alias="GATE_ENV")
    gate_default_mode: str = Field(default="manual", validation_alias="GATE_DEFAULT_MODE")
    action_bearer_token: str = Field(default="", validation_alias="ACTION_BEARER_TOKEN")
    admin_bearer_token: str = Field(default="", validation_alias="ADMIN_BEARER_TOKEN")
    gate_mcp_enabled: bool = Field(default=False, validation_alias="GATE_MCP_ENABLED")
    mcp_auth_mode: str = Field(default="temporary_bearer", validation_alias="MCP_AUTH_MODE")
    mcp_authorization_server: str = Field(default="", validation_alias="MCP_AUTHORIZATION_SERVER")
    mcp_public_resource_url: str = Field(default="", validation_alias="MCP_PUBLIC_RESOURCE_URL")
    mcp_oauth_jwks_url: str = Field(default="", validation_alias="MCP_OAUTH_JWKS_URL")
    mcp_oauth_allowed_algorithms: str = Field(
        default="RS256", validation_alias="MCP_OAUTH_ALLOWED_ALGORITHMS"
    )
    mcp_oauth_clock_skew_seconds: int = Field(
        default=30, ge=0, le=300, validation_alias="MCP_OAUTH_CLOCK_SKEW_SECONDS"
    )
    mcp_oauth_workspace_claim: str = Field(
        default="gate_workspace_id", validation_alias="MCP_OAUTH_WORKSPACE_CLAIM"
    )
    mcp_oauth_account_ids_claim: str = Field(
        default="gate_account_ids", validation_alias="MCP_OAUTH_ACCOUNT_IDS_CLAIM"
    )
    mcp_oauth_project_ids_claim: str = Field(
        default="gate_project_ids", validation_alias="MCP_OAUTH_PROJECT_IDS_CLAIM"
    )
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
    gate_telegram_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_TELEGRAM_WORKER_ENABLED",
    )
    gate_alpha_connector_enabled: bool = Field(
        default=False, validation_alias="GATE_ALPHA_CONNECTOR_ENABLED"
    )
    gate_alpha_shadow_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_SHADOW_WORKER_ENABLED",
    )
    gate_alpha_canary_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_CANARY_ENABLED",
    )
    gate_alpha_delivery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_DELIVERY_ENABLED",
    )
    gate_alpha_privacy_mode_confirmed: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_PRIVACY_MODE_CONFIRMED",
    )
    gate_agent_bundle_root: str = Field(
        default="agent_bundles",
        min_length=1,
        max_length=512,
        validation_alias="GATE_AGENT_BUNDLE_ROOT",
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENAI_API_KEY",
    )
    openai_api_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_API_BASE_URL",
    )
    gate_telegram_voice_archive_enabled: bool = Field(
        default=True,
        validation_alias="GATE_TELEGRAM_VOICE_ARCHIVE_ENABLED",
    )
    gate_telegram_voice_transcription_enabled: bool = Field(
        default=False,
        validation_alias="GATE_TELEGRAM_VOICE_TRANSCRIPTION_ENABLED",
    )
    gate_telegram_voice_transcription_model: str = Field(
        default="gpt-4o-mini-transcribe",
        validation_alias="GATE_TELEGRAM_VOICE_TRANSCRIPTION_MODEL",
    )
    gate_telegram_voice_reserve_microusd_per_second: int = Field(
        default=100,
        ge=100,
        le=10_000,
        validation_alias="GATE_TELEGRAM_VOICE_RESERVE_MICROUSD_PER_SECOND",
    )
    gate_telegram_voice_language: str = Field(
        default="",
        max_length=32,
        validation_alias="GATE_TELEGRAM_VOICE_LANGUAGE",
    )
    gate_alpha_model_runtime_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_MODEL_RUNTIME_ENABLED",
    )
    gate_alpha_model_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_MODEL_DATA_GATE_APPROVED",
    )
    gate_alpha_model_max_output_tokens: int = Field(
        default=512,
        ge=64,
        le=4096,
        validation_alias="GATE_ALPHA_MODEL_MAX_OUTPUT_TOKENS",
    )
    gate_alpha_model_timeout_seconds: float = Field(
        default=30,
        ge=1,
        le=120,
        validation_alias="GATE_ALPHA_MODEL_TIMEOUT_SECONDS",
    )
    gate_workspace_id: str = Field(
        default="mark-personal",
        min_length=1,
        max_length=128,
        validation_alias="GATE_WORKSPACE_ID",
    )
    gate_reply_sequence_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_REPLY_SEQUENCE_WORKER_ENABLED",
    )
    gate_reply_sequence_poll_seconds: float = Field(
        default=5,
        ge=1,
        le=30,
        validation_alias="GATE_REPLY_SEQUENCE_POLL_SECONDS",
    )
    gate_reply_send_recovery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_REPLY_SEND_RECOVERY_ENABLED",
    )
    gate_reply_send_recovery_stale_seconds: int = Field(
        default=900,
        ge=900,
        le=86_400,
        validation_alias="GATE_REPLY_SEND_RECOVERY_STALE_SECONDS",
    )
    gate_reply_send_recovery_limit: int = Field(
        default=100,
        ge=1,
        le=100,
        validation_alias="GATE_REPLY_SEND_RECOVERY_LIMIT",
    )

    @property
    def telegram_connectors(self) -> TelegramConnectorRegistry:
        """Return the trusted release-pinned Telegram connector registry."""

        return build_telegram_connector_registry(
            business_bot_username=self.telegram_bot_username,
            alpha_enabled=self.gate_alpha_connector_enabled,
            alpha_bot_username=self.telegram_alpha_bot_username,
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""

    return Settings()
