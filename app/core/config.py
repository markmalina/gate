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

DROPBOX_CALL_INBOX_PATH = "/Alpha/Calls"


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
    telegram_beta_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BETA_BOT_TOKEN"
    )
    telegram_beta_bot_username: str = Field(
        default="", validation_alias="TELEGRAM_BETA_BOT_USERNAME"
    )
    telegram_beta_webhook_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BETA_WEBHOOK_SECRET"
    )
    telegram_become_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BECOME_BOT_TOKEN"
    )
    telegram_become_bot_username: str = Field(
        default="", validation_alias="TELEGRAM_BECOME_BOT_USERNAME"
    )
    telegram_become_webhook_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BECOME_WEBHOOK_SECRET"
    )
    telegram_aqub_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_AQUB_BOT_TOKEN"
    )
    telegram_aqub_bot_username: str = Field(
        default="", validation_alias="TELEGRAM_AQUB_BOT_USERNAME"
    )
    telegram_aqub_webhook_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_AQUB_WEBHOOK_SECRET"
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
    gate_rich_send_enabled: bool = Field(default=False, validation_alias="GATE_RICH_SEND_ENABLED")
    gate_document_send_enabled: bool = Field(
        default=False, validation_alias="GATE_DOCUMENT_SEND_ENABLED"
    )
    gate_document_cleanup_enabled: bool = Field(
        default=True, validation_alias="GATE_DOCUMENT_CLEANUP_ENABLED"
    )
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
    gate_beta_connector_enabled: bool = Field(
        default=False, validation_alias="GATE_BETA_CONNECTOR_ENABLED"
    )
    gate_beta_checkpoint_dispatch_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BETA_CHECKPOINT_DISPATCH_ENABLED",
    )
    gate_beta_checkpoint_github_app_id: str = Field(
        default="",
        validation_alias="GATE_BETA_CHECKPOINT_GITHUB_APP_ID",
    )
    gate_beta_checkpoint_github_installation_id: str = Field(
        default="",
        validation_alias="GATE_BETA_CHECKPOINT_GITHUB_INSTALLATION_ID",
    )
    gate_beta_checkpoint_github_private_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_BETA_CHECKPOINT_GITHUB_PRIVATE_KEY",
    )
    gate_become_connector_enabled: bool = Field(
        default=False, validation_alias="GATE_BECOME_CONNECTOR_ENABLED"
    )
    gate_aqub_receipt_signing_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_AQUB_RECEIPT_SIGNING_KEY",
    )
    gate_aqub_media_forward_enabled: bool = Field(
        default=False,
        validation_alias="GATE_AQUB_MEDIA_FORWARD_ENABLED",
    )
    gate_aqub_food_media_processing_enabled: bool = Field(
        default=False,
        validation_alias="GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED",
    )
    gate_aqub_food_media_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED",
    )
    gate_aqub_food_media_model: str = Field(
        default="gpt-5.6-luna",
        min_length=1,
        max_length=128,
        validation_alias="GATE_AQUB_FOOD_MEDIA_MODEL",
    )
    gate_aqub_food_media_transcription_model: str = Field(
        default="gpt-4o-mini-transcribe",
        pattern=r"^gpt-4o-(?:mini-)?transcribe$",
        validation_alias="GATE_AQUB_FOOD_MEDIA_TRANSCRIPTION_MODEL",
    )
    gate_beta_installation_key: str = Field(
        default="", validation_alias="GATE_BETA_INSTALLATION_KEY"
    )
    gate_beta_shadow_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BETA_SHADOW_WORKER_ENABLED",
    )
    gate_beta_canary_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BETA_CANARY_ENABLED",
    )
    gate_beta_model_profile: str = Field(
        default="gpt-5.6-luna",
        min_length=1,
        max_length=128,
        validation_alias="GATE_BETA_MODEL_PROFILE",
    )
    gate_beta_model_max_output_tokens: int = Field(
        default=512,
        ge=64,
        le=4096,
        validation_alias="GATE_BETA_MODEL_MAX_OUTPUT_TOKENS",
    )
    gate_beta_model_timeout_seconds: float = Field(
        default=30,
        ge=1,
        le=120,
        validation_alias="GATE_BETA_MODEL_TIMEOUT_SECONDS",
    )
    gate_beta_privacy_mode_confirmed: bool = Field(
        default=False,
        validation_alias="GATE_BETA_PRIVACY_MODE_CONFIRMED",
    )
    gate_beta_katya_space_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BETA_KATYA_SPACE_ENABLED",
    )
    gate_beta_katya_space_ready: bool = Field(
        default=False,
        validation_alias="GATE_BETA_KATYA_SPACE_READY",
    )
    gate_beta_katya_delivery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BETA_KATYA_DELIVERY_ENABLED",
    )
    gate_beta_katya_chat_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_BETA_KATYA_CHAT_ID",
    )
    gate_beta_katya_user_id: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_BETA_KATYA_USER_ID",
    )
    gate_beta_katya_consent_sha256: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_BETA_KATYA_CONSENT_SHA256",
    )
    gate_beta_group_capacity: int = Field(
        default=1,
        ge=1,
        le=10_000,
        validation_alias="GATE_BETA_GROUP_CAPACITY",
    )
    gate_become_installation_key: str = Field(
        default="", validation_alias="GATE_BECOME_INSTALLATION_KEY"
    )
    gate_become_shadow_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_SHADOW_WORKER_ENABLED",
    )
    gate_become_canary_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_CANARY_ENABLED",
    )
    gate_become_delivery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_DELIVERY_ENABLED",
    )
    gate_become_privacy_mode_confirmed: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PRIVACY_MODE_CONFIRMED",
    )
    gate_become_group_capacity: int = Field(
        default=1,
        ge=1,
        le=1,
        validation_alias="GATE_BECOME_GROUP_CAPACITY",
    )
    gate_become_i4_chain_execution_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_I4_CHAIN_EXECUTION_ENABLED",
    )
    gate_become_i4_recovery_execution_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_I4_RECOVERY_EXECUTION_ENABLED",
    )
    gate_become_i4_recovery_max_posts: int = Field(
        default=0,
        ge=0,
        le=3,
        validation_alias="GATE_BECOME_I4_RECOVERY_MAX_POSTS",
    )
    gate_become_voice_transcription_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_VOICE_TRANSCRIPTION_ENABLED",
    )
    gate_become_voice_transcription_data_gate_approved: bool = Field(
        default=False,
        validation_alias=("GATE_BECOME_VOICE_TRANSCRIPTION_DATA_GATE_APPROVED"),
    )
    gate_become_voice_transcription_max_calls: int = Field(
        default=0,
        ge=0,
        le=1,
        validation_alias="GATE_BECOME_VOICE_TRANSCRIPTION_MAX_CALLS",
    )
    gate_become_voice_transcription_model: str = Field(
        default="gpt-4o-mini-transcribe",
        validation_alias="GATE_BECOME_VOICE_TRANSCRIPTION_MODEL",
    )
    gate_become_voice_transcription_language: str = Field(
        default="ru",
        min_length=1,
        max_length=32,
        validation_alias="GATE_BECOME_VOICE_TRANSCRIPTION_LANGUAGE",
    )
    gate_become_voice_transcription_reserve_microusd_per_second: int = Field(
        default=100,
        ge=100,
        le=10_000,
        validation_alias=("GATE_BECOME_VOICE_TRANSCRIPTION_RESERVE_MICROUSD_PER_SECOND"),
    )
    gate_become_voice_transcription_timeout_seconds: float = Field(
        default=60,
        ge=5,
        le=120,
        validation_alias=("GATE_BECOME_VOICE_TRANSCRIPTION_TIMEOUT_SECONDS"),
    )
    gate_become_pilot_slice1_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE1_ENABLED",
    )
    gate_become_pilot_slice1_max_operations: int = Field(
        default=0,
        ge=0,
        le=8,
        validation_alias="GATE_BECOME_PILOT_SLICE1_MAX_OPERATIONS",
    )
    gate_become_pilot_slice1_provider_max_calls: int = Field(
        default=0,
        ge=0,
        le=1,
        validation_alias=("GATE_BECOME_PILOT_SLICE1_PROVIDER_MAX_CALLS"),
    )
    gate_become_pilot_slice2_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE2_ENABLED",
    )
    gate_become_pilot_slice2_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE2_DATA_GATE_APPROVED",
    )
    gate_become_pilot_slice2_max_operations: int = Field(
        default=0,
        ge=0,
        le=6,
        validation_alias="GATE_BECOME_PILOT_SLICE2_MAX_OPERATIONS",
    )
    gate_become_pilot_slice2_provider_max_calls: int = Field(
        default=0,
        ge=0,
        le=1,
        validation_alias="GATE_BECOME_PILOT_SLICE2_PROVIDER_MAX_CALLS",
    )
    gate_become_pilot_slice2_model: str = Field(
        default="gpt-5.6-luna",
        min_length=1,
        max_length=128,
        validation_alias="GATE_BECOME_PILOT_SLICE2_MODEL",
    )
    gate_become_pilot_slice2_max_output_tokens: int = Field(
        default=1024,
        ge=128,
        le=4096,
        validation_alias="GATE_BECOME_PILOT_SLICE2_MAX_OUTPUT_TOKENS",
    )
    gate_become_pilot_slice2_timeout_seconds: float = Field(
        default=30,
        ge=1,
        le=120,
        validation_alias="GATE_BECOME_PILOT_SLICE2_TIMEOUT_SECONDS",
    )
    gate_become_pilot_slice3_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE3_ENABLED",
    )
    gate_become_pilot_slice3_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE3_DATA_GATE_APPROVED",
    )
    gate_become_pilot_slice3_delivery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE3_DELIVERY_ENABLED",
    )
    gate_become_pilot_slice3_relations_enabled: bool = Field(
        default=False,
        validation_alias="GATE_BECOME_PILOT_SLICE3_RELATIONS_ENABLED",
    )
    gate_become_pilot_slice3_max_telegram_sends: int = Field(
        default=0,
        ge=0,
        le=2,
        validation_alias="GATE_BECOME_PILOT_SLICE3_MAX_TELEGRAM_SENDS",
    )
    gate_become_pilot_slice3_max_relations_posts: int = Field(
        default=0,
        ge=0,
        le=1,
        validation_alias="GATE_BECOME_PILOT_SLICE3_MAX_RELATIONS_POSTS",
    )
    gate_become_pilot_slice3_bot_installation_identity: str = Field(
        default="",
        validation_alias="GATE_BECOME_PILOT_SLICE3_BOT_INSTALLATION_IDENTITY",
    )
    gate_become_pilot_slice3_space_ref: str = Field(
        default="",
        validation_alias="GATE_BECOME_PILOT_SLICE3_SPACE_REF",
    )
    gate_become_pilot_slice3_relations_url: str = Field(
        default="",
        validation_alias="GATE_BECOME_PILOT_SLICE3_RELATIONS_URL",
    )
    gate_alpha_shadow_worker_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_SHADOW_WORKER_ENABLED",
    )
    gate_alpha_canary_enabled: bool = Field(
        default=False,
        validation_alias="GATE_ALPHA_CANARY_ENABLED",
    )
    gate_alpha_group_capacity: int = Field(
        default=1,
        ge=1,
        le=10_000,
        validation_alias="GATE_ALPHA_GROUP_CAPACITY",
    )
    gate_alpha_membership_replay_limit: int = Field(
        default=0,
        ge=0,
        le=32,
        validation_alias="GATE_ALPHA_MEMBERSHIP_REPLAY_LIMIT",
    )
    gate_beta_checkpoint_oidc_enabled: bool = Field(
        default=True,
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_ENABLED",
    )
    gate_beta_checkpoint_oidc_audience: str = Field(
        default="gate-beta-checkpoint-v1",
        min_length=1,
        max_length=256,
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_AUDIENCE",
    )
    gate_beta_checkpoint_oidc_repository: str = Field(
        default="AlphaCenter/beta",
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_REPOSITORY",
    )
    gate_beta_checkpoint_oidc_repository_id: str = Field(
        default="1312239432",
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_REPOSITORY_ID",
    )
    gate_beta_checkpoint_oidc_repository_owner_id: str = Field(
        default="310893851",
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_REPOSITORY_OWNER_ID",
    )
    gate_beta_checkpoint_oidc_ref: str = Field(
        default="refs/heads/main",
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_REF",
    )
    gate_beta_checkpoint_oidc_workflow_ref: str = Field(
        default=("AlphaCenter/beta/.github/workflows/checkpoint-context.yml@refs/heads/main"),
        validation_alias="GATE_BETA_CHECKPOINT_OIDC_WORKFLOW_REF",
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
    gate_universal_mayak_runtime_enabled: bool = Field(
        default=False,
        validation_alias="GATE_UNIVERSAL_MAYAK_RUNTIME_ENABLED",
    )
    gate_universal_mayak_delivery_enabled: bool = Field(
        default=False,
        validation_alias="GATE_UNIVERSAL_MAYAK_DELIVERY_ENABLED",
    )
    gate_universal_mayak_model_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_UNIVERSAL_MAYAK_MODEL_DATA_GATE_APPROVED",
    )
    gate_universal_mayak_model_profile: str = Field(
        default="gpt-5.6-luna",
        min_length=1,
        max_length=128,
        validation_alias="GATE_UNIVERSAL_MAYAK_MODEL_PROFILE",
    )
    gate_universal_mayak_max_output_tokens: int = Field(
        default=512,
        ge=64,
        le=4096,
        validation_alias="GATE_UNIVERSAL_MAYAK_MAX_OUTPUT_TOKENS",
    )
    gate_universal_mayak_timeout_seconds: float = Field(
        default=30,
        ge=1,
        le=120,
        validation_alias="GATE_UNIVERSAL_MAYAK_TIMEOUT_SECONDS",
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
    gate_dropbox_call_intake_enabled: bool = Field(
        default=False,
        validation_alias="GATE_DROPBOX_CALL_INTAKE_ENABLED",
    )
    dropbox_call_app_key: str = Field(
        default="",
        validation_alias="DROPBOX_CALL_APP_KEY",
    )
    dropbox_call_app_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="DROPBOX_CALL_APP_SECRET",
    )
    dropbox_call_refresh_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="DROPBOX_CALL_REFRESH_TOKEN",
    )
    dropbox_call_account_id: str = Field(
        default="",
        max_length=255,
        validation_alias="DROPBOX_CALL_ACCOUNT_ID",
    )
    dropbox_call_inbox_path: str = Field(
        default=DROPBOX_CALL_INBOX_PATH,
        max_length=1024,
        validation_alias="DROPBOX_CALL_INBOX_PATH",
    )
    gate_dropbox_call_poll_seconds: float = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias="GATE_DROPBOX_CALL_POLL_SECONDS",
    )
    gate_watch_voice_actions_enabled: bool = Field(
        default=False, validation_alias="GATE_WATCH_VOICE_MARK_OIDC_ENABLED"
    )
    gate_watch_voice_actions_workflow_sha256: str = Field(
        default="",
        max_length=64,
        validation_alias="GATE_WATCH_VOICE_ACTIONS_WORKFLOW_SHA256",
    )
    gate_watch_voice_enabled: bool = Field(
        default=False, validation_alias="GATE_WATCH_VOICE_ENABLED"
    )
    gate_watch_voice_early_inbox_enabled: bool = Field(
        default=False, validation_alias="GATE_WATCH_VOICE_EARLY_INBOX_ENABLED"
    )
    gate_watch_voice_work_reserve_enabled: bool = Field(
        default=False, validation_alias="GATE_WATCH_VOICE_WORK_RESERVE_ENABLED"
    )
    gate_watch_voice_work_mcp_enabled: bool = Field(
        default=False, validation_alias="GATE_WATCH_VOICE_WORK_MCP_ENABLED"
    )
    gate_watch_voice_work_owner_subject: str = Field(
        default="", max_length=255,
        validation_alias="GATE_WATCH_VOICE_WORK_OWNER_SUBJECT",
    )
    gate_watch_voice_work_lease_seconds: int = Field(
        default=1800, ge=60, le=1800,
        validation_alias="GATE_WATCH_VOICE_WORK_LEASE_SECONDS",
    )
    gate_watch_voice_data_gate_approved: bool = Field(
        default=False,
        validation_alias="GATE_WATCH_VOICE_DATA_GATE_APPROVED",
    )
    gate_watch_voice_workspace_key: str = Field(
        default="",
        max_length=128,
        validation_alias="GATE_WATCH_VOICE_WORKSPACE_KEY",
    )
    gate_watch_voice_poll_seconds: float = Field(
        default=30,
        ge=5,
        le=300,
        validation_alias="GATE_WATCH_VOICE_POLL_SECONDS",
    )
    gate_watch_voice_file_id: str = Field(
        default="", max_length=255, validation_alias="GATE_WATCH_VOICE_FILE_ID"
    )
    gate_watch_voice_revision: str = Field(
        default="", max_length=255, validation_alias="GATE_WATCH_VOICE_REVISION"
    )
    gate_mark_github_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GATE_MARK_GITHUB_TOKEN",
    )
    gate_mark_github_writer: str = Field(
        default="",
        max_length=128,
        validation_alias="GATE_MARK_GITHUB_WRITER",
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
            beta_enabled=self.gate_beta_connector_enabled,
            beta_bot_username=self.telegram_beta_bot_username,
            become_enabled=self.gate_become_connector_enabled,
            become_bot_username=self.telegram_become_bot_username,
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""

    return Settings()
