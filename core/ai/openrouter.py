import json
from collections.abc import Sequence
from typing import Protocol

import httpx

from core.ai.persona import ChatTurn

_COMPLETION_PATH = "/chat/completions"
_MAX_TOKENS = 500


class ReplyModel(Protocol):
    async def complete(self, messages: Sequence[ChatTurn]) -> str: ...


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "userbot-manager",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(self, messages: Sequence[ChatTurn]) -> str:
        try:
            response = await self._http.post(
                _COMPLETION_PATH,
                json={
                    "model": self._model,
                    "messages": [turn.model_dump() for turn in messages],
                    "max_tokens": _MAX_TOKENS,
                },
            )
        except httpx.HTTPError as exc:
            raise OpenRouterError("openrouter request failed") from exc
        if response.status_code >= 400:
            raise OpenRouterError(f"openrouter status {response.status_code}")
        return _completion_text(response)


def _completion_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise OpenRouterError("openrouter returned invalid json") from exc
    if not isinstance(payload, dict):
        raise OpenRouterError("openrouter returned an empty completion")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterError("openrouter returned an empty completion")
    first = choices[0]
    if not isinstance(first, dict):
        raise OpenRouterError("openrouter returned an empty completion")
    message = first.get("message")
    if not isinstance(message, dict):
        raise OpenRouterError("openrouter returned an empty completion")
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError("openrouter returned an empty completion")
    return content.strip()
