ACTIVATION_FLAGS = (
    "GATE_AQUB_MEDIA_FORWARD_ENABLED",
    "GATE_AQUB_FOOD_MEDIA_PROCESSING_ENABLED",
    "GATE_AQUB_FOOD_MEDIA_DATA_GATE_APPROVED",
)


def _root_file(name: str) -> str:
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    return (root / name).read_text(encoding="utf-8")


def test_railway_release_enables_only_reviewed_aqub_food_media_gates() -> None:
    config = __import__("tomllib").loads(_root_file("railway.toml"))
    command = config["deploy"]["startCommand"]

    for name in ACTIVATION_FLAGS:
        assert command.count(f"export {name}=true;") == 1

    assert "GATE_AQUB_FOOD_MEDIA_MODEL" not in command
    assert "GATE_AQUB_FOOD_MEDIA_TRANSCRIPTION_MODEL" not in command
    assert "GATE_AQUB_RUNTIME_URL=https://aqub-production.up.railway.app" in command


def test_repository_defaults_remain_fail_closed_outside_the_reviewed_railway_release() -> None:
    example = _root_file(".env.example")
    for name in ACTIVATION_FLAGS:
        assert f"{name}=false" in example
