import pytest
from pydantic import ValidationError

from core.meetings.facts import extract_local, looks_like_yes, parse_meeting_facts
from core.schemas.operator import ProfileContent


def test_local_extract_finds_known_city_and_clock() -> None:
    facts = extract_local("Давайте в Казани в 19:00", ["Казань", "Москва"])
    assert facts.city == "Казань"
    assert facts.when_text == "19:00"
    assert facts.client_confirmed is False


def test_bare_yes_is_confirmation() -> None:
    assert looks_like_yes("Да, конечно")
    assert extract_local("Да", ["Казань"]).client_confirmed is True
    assert looks_like_yes("может завтра в 19:00") is False


def test_model_json_is_parsed_and_garbage_is_empty() -> None:
    facts = parse_meeting_facts('текст {"city": "Сочи", "when_text": "вечером", "client_confirmed": false}')
    assert facts.city == "Сочи"
    assert facts.when_text == "вечером"
    assert parse_meeting_facts("не json").city is None


def test_profile_keeps_phone_and_links_out_of_the_client_text() -> None:
    profile = ProfileContent(
        bio="люблю ночь",
        city="Казань",
        phone="+7 999 000-11-22",
        profile_link="https://t.me/c/1/2",
        photo_album_link="https://t.me/c/1/3",
        video_note_link="https://t.me/c/1/4",
        video_link="https://t.me/c/1/5",
        approx_address="у метро",
    )
    rendered = profile.render("Анна")
    assert "+79990001122" not in rendered
    assert "у метро" not in rendered
    card = profile.render_card("Анна")
    assert "+79990001122" in card
    assert "https://t.me/c/1/3" in card
    assert "у метро" in card
    with pytest.raises(ValidationError):
        ProfileContent(profile_link="не ссылка")
