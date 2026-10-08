import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.constants import MediaKind
from core.db.models import ProfileMedia

_MEDIA_TITLE = {
    MediaKind.PHOTO.value: "фото",
    MediaKind.VIDEO.value: "видео",
    MediaKind.VIDEO_NOTE.value: "кружок",
}


def profile_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁 Показать", callback_data="profile:show")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data="profile:edit")],
            [InlineKeyboardButton(text="В меню", callback_data="menu:main")],
        ]
    )


def profile_editor() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Текст", callback_data="profile:bio")],
            [InlineKeyboardButton(text="🎂 Возраст", callback_data="profile:age")],
            [InlineKeyboardButton(text="🏙 Город", callback_data="profile:city")],
            [InlineKeyboardButton(text="🏷 Теги", callback_data="profile:tags")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:profile")],
        ]
    )


def album_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📷 Фото", callback_data="album:photo")],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="album:video")],
            [InlineKeyboardButton(text="⭕️ Кружок", callback_data="album:video_note")],
            [InlineKeyboardButton(text="👀 Посмотреть", callback_data="album:show")],
            [InlineKeyboardButton(text="В меню", callback_data="menu:main")],
        ]
    )


def prompt_back(callback_data: str = "profile:edit") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=callback_data)]]
    )


def media_keyboard(media_rows: list[ProfileMedia]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for media in media_rows:
        title = _MEDIA_TITLE.get(media.kind, media.kind)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Удалить {title} #{media.position + 1}",
                    callback_data=f"profile:del:{media.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:album")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialog_actions(client_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=f"dialog:reply:{client_id}")],
            [InlineKeyboardButton(text="Завершить", callback_data=f"dialog:close:{client_id}")],
            [InlineKeyboardButton(text="К списку", callback_data="menu:clients")],
        ]
    )


def dialog_reply_back(client_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=f"dialog:open:{client_id}")]]
    )
