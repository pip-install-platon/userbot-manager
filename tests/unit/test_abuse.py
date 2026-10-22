import pytest

from core.moderation.abuse import is_abuse


@pytest.mark.parametrize(
    "text",
    [
        "привет",
        "Казань",
        "сколько стоит",
        "тебе удобно",
        "мебель",
        "хлеб",
        "похудеть",
        "бля",
        "сцена",
        "",
    ],
)
def test_ordinary_text_is_not_abuse(text: str) -> None:
    assert is_abuse(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "нахуй",
        "похуй",
        "иди на хуй",
        "хуйня",
        "охуеть",
        "хуёвый",
        "нахуууй",
        "пизда",
        "минет",
        "ебать",
        "заебал",
        "отсоси",
        "порно",
        "дрочить",
        "блядь",
    ],
)
def test_overt_abuse_matches(text: str) -> None:
    assert is_abuse(text) is True
