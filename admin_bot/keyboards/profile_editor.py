import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.db.models import ProfileMedia


def profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Текст", callback_data="profile:bio")],
            [InlineKeyboardButton(text="Возраст", callback_data="profile:age")],
            [InlineKeyboardButton(text="Город", callback_data="profile:city")],
            [InlineKeyboardButton(text="Теги", callback_data="profile:tags")],
            [InlineKeyboardButton(text="Фото", callback_data="profile:photo")],
            [InlineKeyboardButton(text="Видео", callback_data="profile:video")],
            [InlineKeyboardButton(text="Показать", callback_data="profile:show")],
            [InlineKeyboardButton(text="В меню", callback_data="menu:main")],
        ]
    )


def media_keyboard(media_rows: list[ProfileMedia]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for media in media_rows:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Удалить {media.kind} #{media.position}",
                    callback_data=f"profile:del:{media.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="К анкете", callback_data="menu:profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialog_actions(client_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=f"dialog:reply:{client_id}")],
            [InlineKeyboardButton(text="Завершить", callback_data=f"dialog:close:{client_id}")],
            [InlineKeyboardButton(text="К списку", callback_data="menu:clients")],
        ]
    )
