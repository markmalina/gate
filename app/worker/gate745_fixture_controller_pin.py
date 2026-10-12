"""Fixed C0 trust anchor accepted independently in Delta #305.

This source pin grants no physical effect. Runtime input cannot replace it.
The v1 manifest and ordinary public entry remain UNBOUND/default-off.
"""

from app.worker.gate745_fixture_policy import SHA, FixtureDenied

CONTROLLER_SHA: str | None = "7ec459467b606ac67ec2110289e9018fa322b8fa"
CONTROLLER_TREE: str | None = "83b2a20bbbf467bf89fb8ff28e9a8aa42ca7c2cb"


def controller_pin() -> tuple[str, str]:
    if (
        CONTROLLER_SHA is None
        or CONTROLLER_TREE is None
        or not SHA.fullmatch(CONTROLLER_SHA)
        or not SHA.fullmatch(CONTROLLER_TREE)
    ):
        raise FixtureDenied("E_FIXTURE_V2_UNBOUND")
    return CONTROLLER_SHA, CONTROLLER_TREE
