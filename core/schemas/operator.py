import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.meetings.phone import normalize_phone

_LINK_FIELDS = ("profile_link", "photo_album_link", "video_note_link", "video_link")


class ProfileContent(BaseModel):
    bio: str = Field(default="", max_length=1000)
    age: int | None = Field(default=None, ge=1, le=120)
    city: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list)
    phone: str | None = Field(default=None, max_length=32)
    profile_link: str | None = Field(default=None, max_length=300)
    photo_album_link: str | None = Field(default=None, max_length=300)
    video_note_link: str | None = Field(default=None, max_length=300)
    video_link: str | None = Field(default=None, max_length=300)
    approx_address: str | None = Field(default=None, max_length=300)

    @field_validator("bio")
    @classmethod
    def strip_bio(cls, value: str) -> str:
        return value.strip()

    @field_validator("city")
    @classmethod
    def empty_city_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) > 20:
            raise ValueError("не больше 20 тегов")
        return cleaned

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator(*_LINK_FIELDS)
    @classmethod
    def clean_link(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.startswith(("https://", "http://", "tg://")):
            raise ValueError("ссылка должна начинаться с http://, https:// или tg://")
        return stripped

    @field_validator("approx_address")
    @classmethod
    def clean_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def render(self, display_name: str) -> str:
        lines = [f"Анкета: {display_name}"]
        if self.age is not None:
            lines.append(f"Возраст: {self.age}")
        if self.city:
            lines.append(f"Город: {self.city}")
        if self.bio:
            lines.append(self.bio)
        if self.tags:
            lines.append("Теги: " + ", ".join(self.tags))
        return "\n".join(lines)

    def render_card(self, display_name: str) -> str:
        lines = [
            self.render(display_name),
            "",
            f"Телефон: {self.phone or 'не указан'}",
            f"Ссылка на анкету: {self.profile_link or 'не указана'}",
            f"Ссылка на фото: {self.photo_album_link or 'не указана'}",
            f"Ссылка на кружок: {self.video_note_link or 'не указана'}",
            f"Ссылка на видео: {self.video_link or 'не указана'}",
            f"Примерный адрес: {self.approx_address or 'не указан'}",
        ]
        return "\n".join(lines)


class OperatorPublicSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    telegram_user_id: int
    username: str | None
    display_name: str
    is_superadmin: bool
    is_active: bool
