from collections.abc import Sequence
from typing import Literal, assert_never

from pydantic import BaseModel, Field

from core.ai.examples import DialogExample, retrieve_examples
from core.constants import Direction
from core.schemas.operator import ProfileContent

_ADULT_AGE = 18


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
    examples = retrieve_examples(_latest_incoming(history))
    return _with_history(_system_prompt(display_name, profile, examples), history)


def extraction_turns(history: list[tuple[str, str]]) -> list[ChatTurn]:
    return _with_history(_EXTRACTION_PROMPT, history)


def speakable_profile(profile: ProfileContent) -> ProfileContent:
    if profile.age is not None and profile.age < _ADULT_AGE:
        return profile.model_copy(update={"age": None})
    return profile


def _with_history(system: str, history: list[tuple[str, str]]) -> list[ChatTurn]:
    turns = [ChatTurn(role="system", content=system)]
    for direction, text in history:
        cleaned = text.strip()
        if not cleaned:
            continue
        turns.append(ChatTurn(role=_role(direction), content=cleaned))
    return turns


def _system_prompt(display_name: str, profile: ProfileContent, examples: Sequence[DialogExample]) -> str:
    spoken = speakable_profile(profile)
    who = f"Тебя зовут {display_name}. " if display_name else ""
    card = _card(display_name, spoken)
    card_block = f"\n{card}" if card else ""
    return (
        f"{who}Ты девушка и пишешь в Telegram от первого лица, живо и тепло. Собеседник пишет тебе. "
        "Сглаживай разговор своими словами: коротко, как в переписке, обычно одно-три предложения. "
        "Можно несколько строк, если так естественнее. "
        "Улыбки, разные сердечки и смайлы 😘 ❤️ 💕 😊, уменьшительные вроде «часик» и «солнышко». "
        "Не объясняй, кто ты. Не упоминай смену, занятость и других людей. "
        "Не говори, что кому-то что-то отправляешь или что переписку смотрит кто-то ещё. "
        "Кружочек смотришь ты сама. Сама его не выпрашивай: просьба придёт отдельной фразой. "
        "Не проси написать слово «да» и не говори канцеляритом. "
        "Прайс знаешь наизусть: 12000 часик, 20000 два часика, есть подружка❤️, "
        "выезд от двух часиков плюс такси 🚕 26 k. "
        "Называй его своими словами, когда разговор сам к этому приходит. "
        "Если человек будет нетрезвый или под чем-то, встречи не будет: скажи это один раз мягко. "
        "Не называй номер телефона и точный адрес. "
        "Не обещай фото и видео в этом сообщении. "
        "Не выдумывай возраст, город и факты, которых нет в анкете. "
        "Если не хватает города или примерного времени, спроси только это, ласково. "
        "По тону пойми, человек правда собирается встретиться или просто болтает."
        f"{card_block}\n"
        f"{_examples_block(examples)}"
    )


def _card(display_name: str, profile: ProfileContent) -> str:
    if not display_name and profile.age is None and not profile.city and not profile.bio and not profile.tags:
        return ""
    return profile.render(display_name or "ты")


def _examples_block(examples: Sequence[DialogExample]) -> str:
    if not examples:
        return ""
    lines = ["Примеры коротких ответов:"]
    for example in examples:
        lines.append(f"— {example.client}")
        lines.append(f"— {example.reply}")
    return "\n".join(lines)


def _latest_incoming(history: list[tuple[str, str]]) -> str:
    for direction, text in reversed(history):
        if direction == Direction.INCOMING.value and text.strip():
            return text.strip()
    return ""


def _role(direction: str) -> Literal["user", "assistant"]:
    match Direction(direction):
        case Direction.INCOMING:
            return "user"
        case Direction.OUTGOING:
            return "assistant"
        case _ as unreachable:
            assert_never(unreachable)
