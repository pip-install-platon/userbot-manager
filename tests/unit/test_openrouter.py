import json

import httpx
import pytest

from core.ai.openrouter import OpenRouterClient, OpenRouterError
from core.ai.persona import ChatTurn, persona_messages
from core.constants import Direction
from core.schemas.operator import ProfileContent
from userbot.handlers.incoming import bare_marker


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
    assert "Возраст: 24" in turns[0].content
    assert "Люблю ночные прогулки" in turns[0].content
    assert "своими словами" in turns[0].content
    assert "12000" in turns[0].content
    assert "20000" in turns[0].content
    assert "26 k" in turns[0].content
    assert "подружка" in turns[0].content
    assert "нетрезвый" in turns[0].content
    assert "Примеры коротких ответов:" in turns[0].content
    assert "на смене" not in turns[0].content
    assert "кто свобод" not in turns[0].content
    assert "бот" not in turns[0].content.casefold()
    assert "оператор" not in turns[0].content.casefold()
    assert "перешл" not in turns[0].content.casefold()
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
    assert body["temperature"] == 0.4
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
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


def test_underage_profile_is_not_spoken() -> None:
    turns = persona_messages(
        display_name="Питон",
        profile=ProfileContent(bio="анкета", age=14, city="Казань"),
        history=[(Direction.INCOMING.value, "привет")],
    )
    assert "14" not in turns[0].content
    assert "анкета" in turns[0].content
    assert "Казань" in turns[0].content


def test_bare_message_gets_a_marker() -> None:
    assert bare_marker("sticker") == "[стикер]"
    assert bare_marker("voice") == "[голосовое]"
    assert bare_marker(None) == "[сообщение]"
    assert bare_marker("unknown") == "[сообщение]"


async def test_openrouter_warm_keeps_the_connection_ready() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(503, json={"error": {"message": "secret"}})

    client = OpenRouterClient(
        api_key="test-key",
        model="google/gemini-3.5-flash",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.warm() is True
    finally:
        await client.aclose()
    assert seen == ["/api/v1/models"]


async def test_openrouter_extract_uses_zero_temperature() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        payload = '{"city": "Казань", "when_text": null, "client_confirmed": false}'
        return httpx.Response(200, json={"choices": [{"message": {"content": payload}}]})

    client = OpenRouterClient(
        api_key="test-key",
        model="google/gemini-3.5-flash",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    try:
        facts = await client.extract([("in", "я в Казани")])
    finally:
        await client.aclose()
    assert facts.city == "Казань"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.0
