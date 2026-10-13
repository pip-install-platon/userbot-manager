from typing import Literal, assert_never

from pydantic import BaseModel, Field

from core.constants import Direction
from core.schemas.operator import ProfileContent


class ChatTurn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


def persona_messages(
    *,
    display_name: str,
    profile: ProfileContent,
    history: list[tuple[str, str]],
) -> list[ChatTurn]:
    turns = [ChatTurn(role="system", content=_system_prompt(display_name, profile))]
    for direction, text in history:
        cleaned = text.strip()
        if not cleaned:
            continue
        turns.append(ChatTurn(role=_role(direction), content=cleaned))
    return turns


def _system_prompt(display_name: str, profile: ProfileContent) -> str:
    return (
        f"Ты переписываешься в Telegram от лица {display_name}. "
        "Отвечай коротко, живым языком, на языке собеседника. "
        "Не говори, что ты модель или программа. "
        "Не обещай прислать фото, видео или кружок: их отправляет отдельная система.\n"
        f"{profile.render(display_name)}"
    )


def _role(direction: str) -> Literal["user", "assistant"]:
    match Direction(direction):
        case Direction.INCOMING:
            return "user"
        case Direction.OUTGOING:
            return "assistant"
        case _ as unreachable:
            assert_never(unreachable)
