"""Bounded multi-group admission tests for the Alpha connector."""

import pytest
from app.core.config import Settings
from app.infrastructure.alpha_canary import (
    MAX_ALPHA_GROUP_CAPACITY,
    SessionFactoryAlphaCanaryStore,
    alpha_group_capacity_available,
)


def test_alpha_group_capacity_defaults_to_single_group() -> None:
    settings = Settings(_env_file=None)

    assert settings.gate_alpha_group_capacity == 1


def test_alpha_group_capacity_accepts_bounded_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATE_ALPHA_GROUP_CAPACITY", "3")

    settings = Settings(_env_file=None)

    assert settings.gate_alpha_group_capacity == 3


@pytest.mark.parametrize(
    ("existing", "candidate", "capacity", "allowed"),
    [
        ([], "group-a", 1, True),
        (["group-a"], "group-a", 1, True),
        (["group-a"], "group-b", 1, False),
        (["group-a"], "group-b", 3, True),
        (["group-a", "group-b", "group-c"], "group-d", 3, False),
    ],
)
def test_alpha_group_capacity_is_bounded_and_idempotent(
    existing: list[str],
    candidate: str,
    capacity: int,
    allowed: bool,
) -> None:
    assert (
        alpha_group_capacity_available(
            existing_provider_dialog_ids=existing,
            provider_dialog_id=candidate,
            capacity=capacity,
        )
        is allowed
    )


def test_alpha_runtime_accepts_the_settings_upper_bound() -> None:
    store = SessionFactoryAlphaCanaryStore(
        object(),  # type: ignore[arg-type]
        group_capacity=MAX_ALPHA_GROUP_CAPACITY,
    )

    assert store is not None
    assert alpha_group_capacity_available(
        existing_provider_dialog_ids=["group-a"],
        provider_dialog_id="group-b",
        capacity=MAX_ALPHA_GROUP_CAPACITY,
    )


@pytest.mark.parametrize("capacity", [0, MAX_ALPHA_GROUP_CAPACITY + 1])
def test_alpha_group_capacity_rejects_invalid_bounds(capacity: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 10000"):
        SessionFactoryAlphaCanaryStore(
            object(),  # type: ignore[arg-type]
            group_capacity=capacity,
        )
    with pytest.raises(ValueError, match="between 1 and 10000"):
        alpha_group_capacity_available(
            existing_provider_dialog_ids=[],
            provider_dialog_id="group-a",
            capacity=capacity,
        )
