from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.constants import OperatorStatus

STATUS_ORDER: tuple[OperatorStatus, ...] = (
    OperatorStatus.BUSY,
    OperatorStatus.FREE,
    OperatorStatus.PAUSED,
)

STATUS_TITLE: dict[OperatorStatus, str] = {
    OperatorStatus.BUSY: "🔴 Занят 🔴",
    OperatorStatus.FREE: "🟢 Свободен 🟢",
    OperatorStatus.PAUSED: "💤 Не работаю",
}


def parse_status(raw: str | None) -> OperatorStatus:
    if raw is None:
        return OperatorStatus.PAUSED
    try:
        return OperatorStatus(raw)
    except ValueError:
        return OperatorStatus.PAUSED


def shift_status(current: OperatorStatus, step: int) -> OperatorStatus:
    index = STATUS_ORDER.index(current)
    return STATUS_ORDER[(index + step) % len(STATUS_ORDER)]


def main_menu(is_superadmin: bool, status: OperatorStatus) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="<", callback_data="status:prev"),
            InlineKeyboardButton(text=STATUS_TITLE[status], callback_data="status:current"),
            InlineKeyboardButton(text=">", callback_data="status:next"),
        ],
        [InlineKeyboardButton(text="📝 Моя анкета", callback_data="menu:profile")],
        [InlineKeyboardButton(text="📸 Альбом", callback_data="menu:album")],
        [InlineKeyboardButton(text="💬 Мои клиенты", callback_data="menu:clients")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings")],
    ]
    if is_superadmin:
        rows.append([InlineKeyboardButton(text="🛡 Операторы", callback_data="menu:superadmin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="menu:main")]]
    )
