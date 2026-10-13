from typing import assert_never

from core.constants import MeetingStatus


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


def desk_template(*, city: str | None, when_text: str | None, status: MeetingStatus) -> str:
    match status:
        case MeetingStatus.ADDRESS_SENT:
            return "Если встреча в силе, напишите «да»."
        case MeetingStatus.PHONE_SHARED | MeetingStatus.CONFIRMED:
            return "Если нужно что-то уточнить по встрече — напишите."
        case MeetingStatus.DISCUSSION | MeetingStatus.TENTATIVE:
            return _ask(city, when_text)
        case MeetingStatus.COMPLETED | MeetingStatus.CANCELLED:
            return _ask(None, None)
        case _ as unreachable:
            assert_never(unreachable)


def explain_address(address: str) -> str:
    return f"Можем встретиться примерно здесь: {address}. Напишите «да», если это время и место вам подходят."


def explain_address_missing() -> str:
    return "Адрес ещё уточняю. Напишу, как только будет понятно, куда подходить."


def explain_phone(phone: str) -> str:
    return f"Встреча в силе. Позвоните, чтобы окончательно договориться: {phone}"


def explain_phone_missing() -> str:
    return "Встреча в силе. Номер для звонка сейчас уточняю и сразу пришлю."


def explain_reschedule(when_text: str | None) -> str:
    when = when_text or "это время"
    return (
        f"По времени «{when}» лучше перенести встречу. "
        "Напишите, пожалуйста, другой день и примерно во сколько вам удобно."
    )


def explain_cancel(city: str | None) -> str:
    place = f" в городе {city}" if city else ""
    return f"Встречу{place} придётся отменить. Если захотите договориться снова — просто напишите."


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
        return f"Записала: {city}, примерно {when_text}. Посмотрю, кто свободен, и пришлю адрес."
    if city:
        return f"Город {city} записала. Напишите, примерно во сколько удобно."
    if when_text:
        return f"Время {when_text} записала. Напишите город."
    return "Здравствуйте. Напишите город и примерно во сколько вам удобно встретиться."
