"""Security boundary tests for the pre-provisioned Wedding Beta Space."""

from app.infrastructure import beta_wedding_binding


def test_wedding_binding_has_no_unanchored_creation_surface() -> None:
    for name in (
        "extract_beta_wedding_source_candidate",
        "extract_beta_wedding_activation_candidate",
        "find_beta_wedding_source_candidates",
        "find_beta_wedding_group_candidates",
        "prepare_beta_wedding_binding",
    ):
        assert not hasattr(beta_wedding_binding, name)


def test_wedding_binding_module_keeps_only_immutable_space_identity() -> None:
    assert beta_wedding_binding.BETA_WEDDING_SPACE_REF.startswith("spc_")
    assert beta_wedding_binding.BETA_WEDDING_TENANT_REF.startswith("beta-user-")
    assert not hasattr(beta_wedding_binding, "BETA_WEDDING_GROUP_TITLE")
    assert not hasattr(beta_wedding_binding, "BETA_WEDDING_ACTIVATION_TEXT")
