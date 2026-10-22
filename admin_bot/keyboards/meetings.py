import uuid
from typing import assert_never

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.constants import MeetingStatus


def offer_keyboard(meeting_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Принять", callback_data=f"meet:accept:{meeting_id}")]]
    )


def meetings_list(items: list[tuple[uuid.UUID, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"meet:open:{meeting_id}")] for meeting_id, label in items]
    rows.append([InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def meeting_keyboard(
    meeting_id: uuid.UUID,
    status: MeetingStatus,
    *,
    client_ready: bool,
) -> InlineKeyboardMarkup:
    token = str(meeting_id)
    rows: list[list[InlineKeyboardButton]] = []
    match status:
        case MeetingStatus.ADDRESS_SENT:
            rows.append([InlineKeyboardButton(text="Отправить адрес", callback_data=f"meet:address:{token}")])
            if client_ready:
                rows.append([InlineKeyboardButton(text="Отправить номер", callback_data=f"meet:phone:{token}")])
            rows.extend(_change_rows(token))
        case MeetingStatus.PHONE_SHARED:
            rows.append([InlineKeyboardButton(text="Подтвердил звонком", callback_data=f"meet:call:{token}")])
            rows.extend(_change_rows(token))
        case MeetingStatus.CONFIRMED:
            rows.append([InlineKeyboardButton(text="Завершить встречу", callback_data=f"meet:done:{token}")])
            rows.extend(_change_rows(token))
        case MeetingStatus.DISCUSSION | MeetingStatus.TENTATIVE | MeetingStatus.COMPLETED | MeetingStatus.CANCELLED:
            rows = []
        case _ as unreachable:
            assert_never(unreachable)
    rows.append([InlineKeyboardButton(text="К встречам", callback_data="menu:meetings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _change_rows(token: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="Перенести", callback_data=f"meet:move:{token}")],
        [InlineKeyboardButton(text="Отменить", callback_data=f"meet:drop:{token}")],
    ]
