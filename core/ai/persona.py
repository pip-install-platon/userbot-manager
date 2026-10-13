from typing import Literal, assert_never

from pydantic import BaseModel, Field

from core.constants import Direction
from core.schemas.operator import ProfileContent


class ChatTurn(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


_EXTRACTION_PROMPT = (
    "По переписке верни только JSON без пояснений: "
    '{"city": string|null, "when_text": string|null, "client_confirmed": boolean}. '
    "city — город встречи, when_text — примерное время своими словами, "
    "client_confirmed — собеседник явно согласился, что встреча состоится."
)


def persona_messages(
    *,
    display_name: str,
    profile: ProfileContent,
    history: list[tuple[str, str]],
) -> list[ChatTurn]:
    return _with_history(_system_prompt(display_name, profile), history)


def desk_messages(
    *,
    cities: list[str],
    returning: bool,
    history: list[tuple[str, str]],
) -> list[ChatTurn]:
    where = ", ".join(cities) if cities else "пока никто не на смене"
    known = "Этот человек уже писал раньше. " if returning else ""
    system = (
        "Ты договариваешься о встрече в Telegram. "
        "Пиши коротко, живым языком, на языке собеседника. "
        "Не говори, что ты программа или модель. "
        "Не называй номер телефона и точный адрес: их отправит система отдельно. "
        "Нужны город и примерное время. "
        f"{known}Свободные города: {where}."
    )
    return _with_history(system, history)


def extraction_turns(history: list[tuple[str, str]]) -> list[ChatTurn]:
    return _with_history(_EXTRACTION_PROMPT, history)


def _with_history(system: str, history: list[tuple[str, str]]) -> list[ChatTurn]:
    turns = [ChatTurn(role="system", content=system)]
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
