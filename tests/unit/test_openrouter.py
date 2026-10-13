import json

import httpx
import pytest

from core.ai.openrouter import OpenRouterClient, OpenRouterError
from core.ai.persona import ChatTurn, persona_messages
from core.constants import Direction
from core.schemas.operator import ProfileContent


def test_persona_uses_profile_and_dialog_roles() -> None:
    turns = persona_messages(
        display_name="Анна",
        profile=ProfileContent(bio="Люблю ночные прогулки", age=24, city="Казань"),
        history=[
            (Direction.INCOMING.value, "Привет"),
            (Direction.OUTGOING.value, "И тебе"),
            (Direction.INCOMING.value, "  "),
        ],
    )
    assert turns[0].role == "system"
    assert "Анна" in turns[0].content
    assert "Казань" in turns[0].content
    assert "Люблю ночные прогулки" in turns[0].content
    assert [(turn.role, turn.content) for turn in turns[1:]] == [
        ("user", "Привет"),
        ("assistant", "И тебе"),
    ]


async def test_openrouter_posts_model_and_strips_completion() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "  живой ответ  "}}]},
        )

    client = OpenRouterClient(
        api_key="test-key",
        model="anthropic/claude-3.5-haiku",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        text = await client.complete([ChatTurn(role="user", content="привет")])
    finally:
        await client.aclose()
    assert text == "живой ответ"
    assert seen["authorization"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "anthropic/claude-3.5-haiku"
    assert body["max_tokens"] == 2000
    assert body["messages"] == [{"role": "user", "content": "привет"}]


async def test_openrouter_hides_error_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "secret-body"}})

    client = OpenRouterClient(
        api_key="test-key",
        model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(OpenRouterError) as caught:
            await client.complete([ChatTurn(role="user", content="привет")])
    finally:
        await client.aclose()
    assert "secret-body" not in str(caught.value)
    assert "401" in str(caught.value)
