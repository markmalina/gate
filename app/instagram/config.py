"""Fail-closed Instagram runtime configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstagramAccountConfig:
    alias: str
    instagram_user_id: str
    access_token: str


@dataclass(frozen=True, slots=True)
class InstagramRuntimeConfig:
    enabled: bool
    delivery_enabled: bool
    graph_base_url: str
    graph_version: str
    signing_secret: str
    accounts: dict[str, InstagramAccountConfig]

    @classmethod
    def from_env(cls) -> "InstagramRuntimeConfig":
        accounts: dict[str, InstagramAccountConfig] = {}
        raw = os.getenv("GATE_INSTAGRAM_ACCOUNTS_JSON", "")
        if raw:
            try:
                payload = json.loads(raw)
                for alias, item in payload.items():
                    accounts[alias] = InstagramAccountConfig(
                        alias=alias,
                        instagram_user_id=str(item["instagram_user_id"]),
                        access_token=str(item["access_token"]),
                    )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                accounts = {}
        return cls(
            enabled=os.getenv("GATE_INSTAGRAM_ENABLED", "false").lower() == "true",
            delivery_enabled=os.getenv(
                "GATE_INSTAGRAM_DELIVERY_ENABLED", "false"
            ).lower()
            == "true",
            graph_base_url=os.getenv(
                "GATE_INSTAGRAM_GRAPH_BASE_URL", "https://graph.instagram.com"
            ).rstrip("/"),
            graph_version=os.getenv("GATE_INSTAGRAM_GRAPH_VERSION", ""),
            signing_secret=os.getenv("GATE_INSTAGRAM_SIGNING_SECRET", ""),
            accounts=accounts,
        )

    @property
    def ready(self) -> bool:
        return (
            self.enabled
            and bool(self.graph_version)
            and bool(self.signing_secret)
            and bool(self.accounts)
        )

    def account(self, alias: str) -> InstagramAccountConfig | None:
        return self.accounts.get(alias)
