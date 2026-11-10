ACTIVATION_FLAGS = (
    "GATE_AQUB_MEDIA_FORWARD_ENABLED",
    "GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED",
    "GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED",
)


def _root_file(name: str) -> str:
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    return (root / name).read_text(encoding="utf-8")


def test_railway_release_has_no_aqub_activation_or_endpoint() -> None:
    config = __import__("tomllib").loads(_root_file("railway.toml"))
    command = config["deploy"]["startCommand"]
    assert "AQUB" not in command
    assert "aqub" not in command


def test_repository_defaults_remain_fail_closed_outside_the_reviewed_railway_release() -> None:
    example = _root_file(".env.example")
    for name in ACTIVATION_FLAGS:
        assert f"{name}=false" in example

