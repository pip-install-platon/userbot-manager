from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(is_superadmin: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🟢 Я свободен", callback_data="status:free"),
            InlineKeyboardButton(text="🔴 Я занят", callback_data="status:busy"),
        ],
        [InlineKeyboardButton(text="⏸ Пауза", callback_data="status:paused")],
        [InlineKeyboardButton(text="📝 Моя анкета", callback_data="menu:profile")],
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
