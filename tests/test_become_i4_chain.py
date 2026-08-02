from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from app.application.become_i4_chain import (
    I4_CONTRACT_DIGEST,
    BecomeI4ContractError,
    BecomeI4CorrelationEvidence,
    BecomeI4Stage,
    build_mayaki_request,
    build_oko_request,
    build_relations_request,
    build_take_request,
    canonical_digest,
    project_i4_refs,
    validate_i4_receipt,
)
from app.infrastructure.become_i4_chain import (
    MAYAKI_ENDPOINT,
    MAYAKI_SOURCE_SHA,
    OKO_SOURCE_SHA,
    RELATIONS_SOURCE_SHA,
    TAKE_SOURCE_SHA,
    BecomeI4ProductionTargets,
    BecomeI4ServiceTarget,
    BecomeI4TransportState,
    PostReservedBecomeI4StageOnce,
    ReservedBecomeI4Stage,
)


def _evidence(**overrides: str) -> BecomeI4CorrelationEvidence:
    values = {
        "correlation_ref": "synthetic://correlation/voice/" + "1" * 32,
        "consent_quest_ref": "quest_become_investor_demo_v1",
        "gate_subject_ref": "person_gate_" + "2" * 24,
        "voice_intake_digest": "3" * 64,
        "transcript_digest": "4" * 64,
        "consent_receipt_digest": "5" * 64,
        "relations_execution_digest": "6" * 64,
        "relations_domain_event_id": "consent_evt_" + "7" * 24,
        "relations_domain_event_digest": "8" * 64,
    }
    values.update(overrides)
    return BecomeI4CorrelationEvidence(**values)


def _receipt(
    stage: BecomeI4Stage,
    request: Mapping[str, object],
    output: dict[str, object],
) -> dict[str, object]:
    source = {
        BecomeI4Stage.RELATIONS: RELATIONS_SOURCE_SHA,
        BecomeI4Stage.MAYAKI: MAYAKI_SOURCE_SHA,
        BecomeI4Stage.OKO: OKO_SOURCE_SHA,
        BecomeI4Stage.TAKE: TAKE_SOURCE_SHA,
    }[stage]
    value: dict[str, object] = {
        "schema": "become.i4-service-receipt@1",
        "service": stage.value,
        "source_sha": source,
        "contract_digest": I4_CONTRACT_DIGEST,
        "mode": request["mode"],
        "quest_ref": request["quest_ref"],
        "correlation_ref": request["correlation_ref"],
        "subject_ref": request["subject_ref"],
        "external_effects": 0,
        "output": output,
    }
    value["receipt_digest"] = canonical_digest(value)
    return value


def _chain() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    evidence = _evidence()
    refs = project_i4_refs(evidence)
    relations_request = build_relations_request(evidence, refs)
    relations = _receipt(
        BecomeI4Stage.RELATIONS,
        relations_request,
        {
            "person_ref": "synthetic://relations/person/" + "a" * 32,
            "alias_count": 2,
            "person_count": 1,
            "consent_receipt_ref": refs.consent_receipt_ref,
            "eligibility_receipt_ref": "synthetic://relations/eligibility/" + "b" * 32,
            "referral_edge_ref": refs.referral_edge_ref,
            "attribution_scope": "PERSON",
        },
    )
    mayaki_request = build_mayaki_request(evidence, refs, relations)
    mayaki = _receipt(
        BecomeI4Stage.MAYAKI,
        mayaki_request,
        {
            "profile_version_ref": "synthetic://mayaki/profile/1",
            "product_version_ref": "synthetic://mayaki/product/1",
            "program_version_ref": "synthetic://mayaki/program/1",
            "exercise_version_ref": "synthetic://mayaki/exercise/1",
            "approval_receipt_ref": "synthetic://mayaki/approval/1",
            "enrollment_ref": "synthetic://mayaki/enrollment/1",
            "entitlement_ref": "synthetic://mayaki/entitlement/1",
            "immutable_version_graph": True,
        },
    )
    oko_request = build_oko_request(evidence, refs, relations, mayaki)
    oko = _receipt(
        BecomeI4Stage.OKO,
        oko_request,
        {
            "exercise_session_ref": "synthetic://oko/session/1",
            "submission_ref": "synthetic://oko/submission/1",
            "result_receipt_ref": "synthetic://oko/result/1",
            "duration_seconds": 600,
            "free_result_delivered": True,
            "restart_safe": True,
            "duplicate_effects": 0,
        },
    )
    built_take = build_take_request(evidence, refs, relations, mayaki, oko)
    take = _receipt(
        BecomeI4Stage.TAKE,
        built_take.request,
        {
            "transcript_version_ref": "synthetic://take/transcript-version/1",
            "render_ref": "synthetic://take/render/1",
            "publication_ref": "synthetic://take/publication-disabled/1",
            "exact_approval": True,
            "byte_count": 0,
            "publication_state": "disabled",
            "privacy_closure_receipt_ref": "synthetic://take/privacy-closure/1",
        },
    )
    return relations, mayaki, oko, take


def test_projects_real_gate_identity_without_forwarding_gate_refs() -> None:
    evidence = _evidence()
    first = project_i4_refs(evidence)
    second = project_i4_refs(evidence)
    assert first == second
    assert first.subject_ref.startswith("synthetic://alias/telegram/")
    assert first.voice_alias_ref.startswith("synthetic://alias/voice/")
    assert "/consent/" in first.consent_receipt_ref
    assert evidence.gate_subject_ref not in repr(first)
    assert evidence.consent_quest_ref not in repr(first)


def test_builds_and_validates_all_four_exact_requests_and_receipts() -> None:
    evidence = _evidence()
    refs = project_i4_refs(evidence)
    relations, mayaki, oko, take = _chain()
    requests = (
        build_relations_request(evidence, refs),
        build_mayaki_request(evidence, refs, relations),
        build_oko_request(evidence, refs, relations, mayaki),
        build_take_request(evidence, refs, relations, mayaki, oko).request,
    )
    sources = (
        RELATIONS_SOURCE_SHA,
        MAYAKI_SOURCE_SHA,
        OKO_SOURCE_SHA,
        TAKE_SOURCE_SHA,
    )
    for stage, request, receipt, source in zip(
        BecomeI4Stage, requests, (relations, mayaki, oko, take), sources, strict=True
    ):
        assert set(request) == {
            "schema",
            "contract_digest",
            "mode",
            "quest_ref",
            "correlation_ref",
            "subject_ref",
            "input",
        }
        assert validate_i4_receipt(
            stage=stage,
            receipt=receipt,
            request=request,
            expected_source_sha=source,
        ) == receipt


def test_take_source_and_mapping_bind_oko_result_directionally() -> None:
    evidence = _evidence()
    refs = project_i4_refs(evidence)
    relations, mayaki, oko, _take = _chain()
    first = build_take_request(evidence, refs, relations, mayaki, oko)
    changed = dict(oko)
    changed_output = dict(changed["output"])
    changed_output["result_receipt_ref"] = "synthetic://oko/result/2"
    changed["output"] = changed_output
    second = build_take_request(evidence, refs, relations, mayaki, changed)
    assert first.source_asset_ref.startswith("synthetic://gate/source/")
    assert first.source_asset_ref != second.source_asset_ref
    assert first.mapping_digest != second.mapping_digest


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"external_effects": 1}, "service_receipt_binding_invalid"),
        ({"source_sha": "f" * 40}, "service_receipt_binding_invalid"),
        ({"unexpected": True}, "service_receipt_fields_invalid"),
    ],
)
def test_receipt_validator_fails_closed_on_mutation(
    mutation: dict[str, object], code: str
) -> None:
    evidence = _evidence()
    refs = project_i4_refs(evidence)
    request = build_relations_request(evidence, refs)
    relations, _mayaki, _oko, _take = _chain()
    mutated = {**relations, **mutation}
    with pytest.raises(BecomeI4ContractError, match=code):
        validate_i4_receipt(
            stage=BecomeI4Stage.RELATIONS,
            receipt=mutated,
            request=request,
            expected_source_sha=RELATIONS_SOURCE_SHA,
        )


def test_production_targets_reject_drift_or_public_urls() -> None:
    targets = BecomeI4ProductionTargets()
    assert targets.for_stage(BecomeI4Stage.MAYAKI).endpoint == MAYAKI_ENDPOINT
    with pytest.raises(ValueError, match="exact_production_target_required"):
        BecomeI4ServiceTarget(
            BecomeI4Stage.MAYAKI,
            "https://public.example/internal/become/i4",
            MAYAKI_SOURCE_SHA,
        )
    with pytest.raises(ValueError, match="exact_production_target_required"):
        BecomeI4ServiceTarget(
            BecomeI4Stage.MAYAKI,
            MAYAKI_ENDPOINT,
            "f" * 40,
        )


class FakeClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def post(
        self, url: str, *, json: object, timeout: float
    ) -> httpx.Response:
        self.calls += 1
        assert url.endswith("/internal/become/i4")
        assert timeout == 10.0
        assert isinstance(json, dict)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _reservation(*, duplicate: bool = False) -> tuple[ReservedBecomeI4Stage, dict[str, object]]:
    evidence = _evidence()
    refs = project_i4_refs(evidence)
    request = build_relations_request(evidence, refs)
    return (
        ReservedBecomeI4Stage(
            stage=BecomeI4Stage.RELATIONS,
            request_digest=canonical_digest(request),
            request_payload=request,
            duplicate=duplicate,
        ),
        request,
    )


@pytest.mark.anyio
async def test_transport_completes_one_valid_attempt() -> None:
    reservation, request = _reservation()
    relations, _mayaki, _oko, _take = _chain()
    client = FakeClient(
        response=httpx.Response(200, json=relations, request=httpx.Request("POST", "http://x"))
    )
    outcome = await PostReservedBecomeI4StageOnce(client).execute(
        target=BecomeI4ProductionTargets().relations,
        reservation=reservation,
    )
    assert request == reservation.request_payload
    assert outcome.state is BecomeI4TransportState.COMPLETED
    assert outcome.receipt_payload == relations
    assert client.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "error", "expected"),
    [
        (httpx.Response(400), None, BecomeI4TransportState.FAILED),
        (httpx.Response(500), None, BecomeI4TransportState.UNKNOWN),
        (
            None,
            httpx.ConnectError("connect", request=httpx.Request("POST", "http://x")),
            BecomeI4TransportState.FAILED,
        ),
        (
            None,
            httpx.ReadTimeout("read", request=httpx.Request("POST", "http://x")),
            BecomeI4TransportState.UNKNOWN,
        ),
    ],
)
async def test_transport_classifies_definite_and_ambiguous_outcomes(
    response: httpx.Response | None,
    error: Exception | None,
    expected: BecomeI4TransportState,
) -> None:
    reservation, _request = _reservation()
    if response is not None:
        response.request = httpx.Request("POST", "http://x")
    client = FakeClient(response=response, error=error)
    outcome = await PostReservedBecomeI4StageOnce(client).execute(
        target=BecomeI4ProductionTargets().relations,
        reservation=reservation,
    )
    assert outcome.state is expected
    assert outcome.receipt_payload is not None
    assert client.calls == 1


@pytest.mark.anyio
async def test_invalid_success_receipt_is_unknown_and_not_retried() -> None:
    reservation, _request = _reservation()
    client = FakeClient(
        response=httpx.Response(
            200,
            json={"invalid": True},
            request=httpx.Request("POST", "http://x"),
        )
    )
    outcome = await PostReservedBecomeI4StageOnce(client).execute(
        target=BecomeI4ProductionTargets().relations,
        reservation=reservation,
    )
    assert outcome.state is BecomeI4TransportState.UNKNOWN
    assert outcome.reconciliation_required is True
    assert client.calls == 1


@pytest.mark.anyio
async def test_duplicate_reservation_is_never_sent() -> None:
    reservation, _request = _reservation(duplicate=True)
    client = FakeClient(error=AssertionError("must not call"))
    outcome = await PostReservedBecomeI4StageOnce(client).execute(
        target=BecomeI4ProductionTargets().relations,
        reservation=reservation,
    )
    assert outcome.state is BecomeI4TransportState.RECONCILIATION_REQUIRED
    assert outcome.attempted is False
    assert outcome.reconciliation_required is True
    assert client.calls == 0
