import httpx
import pytest
from app.infrastructure.beta_wedding_voice_provider import OpenAIBetaWeddingVoiceStructurer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_structurer_reads_output_text_from_raw_responses_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    '{"text":"Я поняла так:\\n'
                                    '- Факты: площадка нужна на 40 гостей.\\n'
                                    '- Решения: не выделены.\\n'
                                    '- Задачи: подобрать площадку.\\n'
                                    '- Вопросы: бюджет не указан."}'
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        structurer = OpenAIBetaWeddingVoiceStructurer(
            api_key="fixture-key",
            api_base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            client=client,
        )

        result = await structurer.structure(
            transcript="Нужна площадка на сорок гостей."
        )

    assert result.startswith("Я поняла так:")
    assert "подобрать площадку" in result
