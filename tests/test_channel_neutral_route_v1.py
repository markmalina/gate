"""Adversarial proofs for the provider-free channel-neutral route."""

from __future__ import annotations

import socket

import pytest

from app.application.channel_neutral_route_v1 import (
    IngressDisposition,
    LocalRouteJournal,
    RouteBinding,
    RouteError,
    RouteRegistry,
    RouteStage,
    SourceEnvelope,
)


def _registry() -> RouteRegistry:
    return RouteRegistry(
        (
            RouteBinding("route:synthetic0001", "test", frozenset({"actor:synthetic0001"})),
            RouteBinding("route:synthetic0002", "pilot-a", frozenset({"actor:synthetic0002"})),
        )
    )


def _envelope(**changes: object) -> SourceEnvelope:
    value: dict[str, object] = {
        "route_ref": "route:synthetic0001",
        "space_ref": "test",
        "actor_ref": "actor:synthetic0001",
        "source_event_id": "source/synthetic/0001",
        "payload": {"kind": "text", "value": "synthetic"},
    }
    value.update(changes)
    return SourceEnvelope(**value)  # type: ignore[arg-type]


def test_unknown_actor_on_known_route_fails_closed() -> None:
    with pytest.raises(RouteError, match="ACTOR_NOT_ALLOWED"):
        LocalRouteJournal(_registry()).accept(_envelope(actor_ref="actor:synthetic9999"))


def test_known_actor_on_foreign_route_fails_closed() -> None:
    with pytest.raises(RouteError, match="ACTOR_NOT_ALLOWED"):
        LocalRouteJournal(_registry()).accept(
            _envelope(route_ref="route:synthetic0002", space_ref="pilot-a")
        )


def test_unknown_ambiguous_and_non_allowlisted_routes_fail_closed() -> None:
    with pytest.raises(RouteError, match="ROUTE_UNKNOWN"):
        LocalRouteJournal(_registry()).accept(_envelope(route_ref="route:synthetic9999"))
    binding = RouteBinding("route:synthetic0001", "test", frozenset({"actor:synthetic0001"}))
    with pytest.raises(RouteError, match="ROUTE_AMBIGUOUS"):
        RouteRegistry((binding, binding))
    with pytest.raises(RouteError, match="SPACE_NOT_ALLOWED"):
        _envelope(space_ref="default")


def test_duplicate_source_and_delivery_retry_are_deterministic() -> None:
    journal = LocalRouteJournal(_registry())
    first = journal.accept(_envelope())
    duplicate = journal.accept(_envelope())
    assert first.disposition is IngressDisposition.ACCEPTED
    assert duplicate.disposition is IngressDisposition.DUPLICATE
    assert duplicate.entry == first.entry

    committed, _ = journal.commit_with(first.entry.ingress_ref, lambda: "ok")
    prepared = journal.prepare_response(committed.ingress_ref, {"text": "synthetic response"})
    repeated = journal.prepare_response(committed.ingress_ref, {"text": "synthetic response"})
    assert repeated == prepared
    assert prepared.response_ref is not None
    delivered = journal.record_delivery(prepared.response_ref, "delivery:synthetic0001")
    assert journal.record_delivery(prepared.response_ref, "delivery:synthetic0001") == delivered


def test_same_source_with_changed_fact_is_not_treated_as_duplicate() -> None:
    journal = LocalRouteJournal(_registry())
    journal.accept(_envelope())
    with pytest.raises(RouteError, match="SOURCE_EVENT_CONFLICT"):
        journal.accept(_envelope(payload={"kind": "text", "value": "changed"}))


def test_lost_response_is_prepared_not_delivered() -> None:
    journal = LocalRouteJournal(_registry())
    accepted = journal.accept(_envelope()).entry
    committed, _ = journal.commit_with(accepted.ingress_ref, lambda: None)
    prepared = journal.prepare_response(committed.ingress_ref, {"text": "synthetic response"})
    assert prepared.stage is RouteStage.RESPONSE_PREPARED
    assert prepared.delivery_ref is None
    assert journal.get(prepared.ingress_ref).stage is RouteStage.RESPONSE_PREPARED


def test_downstream_failure_leaves_only_accepted_fact() -> None:
    journal = LocalRouteJournal(_registry())
    accepted = journal.accept(_envelope()).entry

    def fail() -> None:
        raise ValueError("synthetic failure")

    with pytest.raises(RouteError, match="DOWNSTREAM_FAILURE"):
        journal.commit_with(accepted.ingress_ref, fail)
    assert journal.get(accepted.ingress_ref).stage is RouteStage.ACCEPTED


def test_cross_space_envelope_fails_closed() -> None:
    with pytest.raises(RouteError, match="CROSS_SPACE_ENVELOPE"):
        LocalRouteJournal(_registry()).accept(_envelope(space_ref="pilot-a"))


def test_attempted_network_access_is_unnecessary_and_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("network/provider access attempted")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    journal = LocalRouteJournal(_registry())
    accepted = journal.accept(_envelope()).entry
    committed, value = journal.commit_with(accepted.ingress_ref, lambda: "local")
    assert value == "local"
    assert committed.stage is RouteStage.COMMITTED


def test_envelope_payload_is_immutable_snapshot() -> None:
    payload = {"kind": "text", "nested": {"value": "before"}}
    envelope = _envelope(payload=payload)
    before = LocalRouteJournal(_registry()).accept(envelope).entry.payload_digest
    payload["nested"]["value"] = "after"
    assert LocalRouteJournal(_registry()).accept(envelope).entry.payload_digest == before
