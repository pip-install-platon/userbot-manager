import json
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, field_validator

_CLOCK = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
_HOUR = re.compile(r"\bв\s+\d{1,2}(?:\s*час(?:а|ов)?)?\b", re.IGNORECASE)
_PART = re.compile(r"\b(утром|днём|днем|вечером|ночью|завтра|сегодня)\b", re.IGNORECASE)
_CITY_WORD = re.compile(r"город[еуа]?\s+([A-Za-zА-Яа-яЁё-]{2,40})", re.IGNORECASE)
_TOKEN = re.compile(r"[a-zа-яё]+", re.IGNORECASE)
_YES = frozenset({"да", "ага", "ок", "окей", "подтверждаю", "приду", "буду", "хорошо", "договорились"})
_FILLER = frozenset(
    {
        "я",
        "конечно",
        "встреча",
        "состоится",
        "это",
        "мне",
        "подходит",
        "давайте",
        "давай",
        "всё",
        "все",
        "так",
        "хорошо",
        "устроит",
    }
)


class MeetingFacts(BaseModel):
    city: str | None = None
    when_text: str | None = None
    client_confirmed: bool = False

    @field_validator("city", "when_text", mode="before")
    @classmethod
    def blank_is_none(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped[:120]


class MeetingExtractor(Protocol):
    async def extract(self, history: Sequence[tuple[str, str]]) -> MeetingFacts: ...


def parse_meeting_facts(raw: str) -> MeetingFacts:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return MeetingFacts()
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return MeetingFacts()
    if not isinstance(payload, dict):
        return MeetingFacts()
    return MeetingFacts.model_validate(
        {
            "city": payload.get("city"),
            "when_text": payload.get("when_text"),
            "client_confirmed": bool(payload.get("client_confirmed")),
        }
    )


def extract_local(text: str, cities: list[str]) -> MeetingFacts:
    city = _city(text, cities)
    when_text = _when(text)
    confirmed = city is None and when_text is None and looks_like_yes(text)
    return MeetingFacts(city=city, when_text=when_text, client_confirmed=confirmed)


def looks_like_yes(text: str) -> bool:
    tokens = {item.casefold() for item in _TOKEN.findall(text)}
    if not tokens:
        return False
    if "подтверждаю" in tokens:
        return True
    if not tokens & _YES:
        return False
    return tokens <= (_YES | _FILLER)


def merge_facts(primary: MeetingFacts, fallback: MeetingFacts) -> MeetingFacts:
    return MeetingFacts(
        city=primary.city or fallback.city,
        when_text=primary.when_text or fallback.when_text,
        client_confirmed=primary.client_confirmed or fallback.client_confirmed,
    )


def _city(text: str, cities: list[str]) -> str | None:
    folded = text.casefold()
    for city in sorted(cities, key=len, reverse=True):
        name = city.casefold()
        # The client often declines the city ("Казани"), so the last letter can differ.
        if name in folded or (len(name) >= 5 and name[:-1] in folded):
            return city
    match = _CITY_WORD.search(text)
    if match is None:
        return None
    return match.group(1)


def _when(text: str) -> str | None:
    for pattern in (_CLOCK, _HOUR, _PART):
        match = pattern.search(text)
        if match is not None:
            return match.group(0).strip()
    return None
