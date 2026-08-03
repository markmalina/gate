from .role_aware_contract import RoleAwareAdapter


def create_role_aware_gate_adapter(service_source_sha: str) -> RoleAwareAdapter:
    """Return the repository-only, default-off Gate validator."""
    return RoleAwareAdapter("GATE", service_source_sha)
