import pytest

from core.meetings.phrases import hello_card, wants_price
from core.moderation.abuse import is_abuse


def test_hello_card_is_the_girl_script() -> None:
    assert hello_card() == (
        "Приветик 😘\n"
        "\n"
        "Стоимость:\n"
        "12000 часик\n"
        "20000 два часика\n"
        "Есть подружка❤️\n"
        "Есть выезд, от двух часиков + оплата такси 🚕 26 k"
    )


@pytest.mark.parametrize(
    "text",
    ["сколько стоит", "цена", "прайс", "почём", "по чем выезд", "стоимость"],
)
def test_price_question_is_recognized(text: str) -> None:
    assert wants_price(text) is True


@pytest.mark.parametrize("text", ["привет", "Казань в 19:00", "сцена", "тебе удобно"])
def test_ordinary_text_is_not_a_price_question(text: str) -> None:
    assert wants_price(text) is False


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
