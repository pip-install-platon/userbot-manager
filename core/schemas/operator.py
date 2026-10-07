import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileContent(BaseModel):
    bio: str = Field(default="", max_length=1000)
    age: int | None = Field(default=None, ge=1, le=120)
    city: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list)

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


class OperatorPublicSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    telegram_user_id: int
    username: str | None
    display_name: str
    is_superadmin: bool
    is_active: bool
