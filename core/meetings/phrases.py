import re
from typing import assert_never

from core.constants import MeetingStatus

PRICE_CARD = (
    "Стоимость:\n"
    "12000 часик\n"
    "20000 два часика\n"
    "Есть подружка❤️\n"
    "Есть выезд, от двух часиков + оплата такси 🚕 26 k"
)

_PRICE_TOKENS = frozenset(
    {
        "сколько",
        "стоит",
        "цена",
        "цене",
        "цену",
        "цены",
        "ценой",
        "прайс",
        "прайса",
        "стоимость",
        "стоимости",
        "почем",
    }
)
_TOKEN = re.compile(r"[а-яa-z0-9]+")


def status_title(status: MeetingStatus) -> str:
    match status:
        case MeetingStatus.DISCUSSION:
            return "обсуждение"
        case MeetingStatus.TENTATIVE | MeetingStatus.ADDRESS_SENT | MeetingStatus.PHONE_SHARED:
            return "условная договоренность"
        case MeetingStatus.CONFIRMED:
            return "подтвержденная"
        case MeetingStatus.COMPLETED:
            return "выполненная"
        case MeetingStatus.CANCELLED:
            return "отменена"
        case _ as unreachable:
            assert_never(unreachable)


def hello_card() -> str:
    return f"Приветик 😘\n\n{PRICE_CARD}"


def price_card() -> str:
    return PRICE_CARD


def opening_reply(city: str | None, when_text: str | None) -> str:
    if city and when_text:
        lead = f"Приветик 😘 {city}, примерно {when_text} 😊"
    elif city:
        lead = f"Приветик 😘 {city}, поняла 💕 Во сколько тебе удобно?"
    elif when_text:
        lead = f"Приветик 😘 {when_text} вижу 😊 А ты из какого города?"
    else:
        return hello_card()
    return f"{lead}\n\n{PRICE_CARD}"


def wants_price(text: str) -> bool:
    folded = text.casefold().replace("ё", "е")
    if "по чем" in folded:
        return True
    return bool(_PRICE_TOKENS.intersection(_TOKEN.findall(folded)))


def desk_template(*, city: str | None, when_text: str | None, status: MeetingStatus) -> str:
    match status:
        case MeetingStatus.ADDRESS_SENT:
            return "Жду кружочек 😘"
        case MeetingStatus.PHONE_SHARED | MeetingStatus.CONFIRMED:
            return "Если что, пиши 💕"
        case MeetingStatus.DISCUSSION | MeetingStatus.TENTATIVE:
            return _ask(city, when_text)
        case MeetingStatus.COMPLETED | MeetingStatus.CANCELLED:
            return _ask(None, None)
        case _ as unreachable:
            assert_never(unreachable)


def explain_address(address: str) -> str:
    return f"Давай тогда около {address} 😊 {ask_circle()}"


def explain_address_missing() -> str:
    return f"Местечко ещё прикину и напишу тебе 💕 {ask_circle()}"


def explain_phone(phone: str) -> str:
    return f"Тогда созваниваемся, мой номер {phone} 😘"


def explain_phone_missing() -> str:
    return "Номер сейчас допишу и кину сюда 💕"


def explain_reschedule(when_text: str | None) -> str:
    when = when_text or "это время"
    return f"Лучше перенести, «{when}» не выходит 😔 Когда тебе нормально, солнышко?"


def explain_cancel(city: str | None) -> str:
    place = f" в городе {city}" if city else ""
    return f"Слушай, встречу{place} придётся отменить 😔 Если надумаешь ещё, просто напиши ❤️"


def ask_circle() -> str:
    return (
        "Скинь кружочек, солнышко, хочу глянуть, что ты в порядке 😘 "
        "Если будешь нетрезвый или под чем-то, встречи не будет."
    )


def circle_received() -> str:
    return "Вижу, спасибо 😘"


def meeting_card(
    *,
    username: str | None,
    telegram_user_id: int,
    status: MeetingStatus,
    city: str | None,
    when_text: str | None,
    client_ready: bool,
) -> str:
    who = f"@{username}" if username else str(telegram_user_id)
    lines = [
        f"Встреча с {who}",
        f"Статус: {status_title(status)}",
        f"Город: {city or '—'}",
        f"Время: {when_text or '—'}",
    ]
    if status == MeetingStatus.ADDRESS_SENT and client_ready:
        lines.append("Клиент подтвердил. Можно отправить номер из анкеты.")
    if status == MeetingStatus.PHONE_SHARED:
        lines.append("Клиент получил номер. После звонка подтвердите встречу.")
    if status == MeetingStatus.CONFIRMED:
        lines.append("Вы заняты этой встречей. После неё отметьте завершение и выставьте статус.")
    return "\n".join(lines)


def _ask(city: str | None, when_text: str | None) -> str:
    if city and when_text:
        return f"Ага, {city}, примерно {when_text} 😊\n\n{PRICE_CARD}"
    if city:
        return f"{city}, поняла 💕 Во сколько тебе удобно, солнышко?"
    if when_text:
        return f"{when_text} вижу 😊 А ты из какого города?"
    return "Приветик 😘 А ты из какого города и во сколько тебе удобно?"
