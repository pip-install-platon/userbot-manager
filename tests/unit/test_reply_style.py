import uuid

from core.ai.examples import retrieve_examples
from core.ai.reply import tighten, usable_reply
from core.ai.voice import VoiceCandidate, choose_voice
from core.schemas.operator import ProfileContent


def test_retrieval_picks_the_closest_example() -> None:
    greeting = retrieve_examples("привет")
    sticker = retrieve_examples("[стикер]")
    meeting = retrieve_examples("казань в 19:00")
    assert greeting[0].client == "привет"
    assert sticker[0].client == "[стикер]"
    assert meeting[0].client == "казань в 19:00"
    assert retrieve_examples("")[0].client == "привет"


def test_reply_stays_short_and_drops_shift_talk() -> None:
    long = "Первое предложение здесь. Второе тоже. Третье лишнее и клиенту не нужно."
    assert tighten(long) == "Первое предложение здесь. Второе тоже."
    assert usable_reply("я свободна вечером") == "я свободна вечером"
    assert usable_reply("Скинь кружочек, я гляну 🙂") == "Скинь кружочек, я гляну 🙂"
    assert usable_reply("Сейчас никто не на смене, все отдыхают.") is None
    assert usable_reply("Уточню у оператора и напишу.") is None
    assert usable_reply("Я бот, перешлю это дальше.") is None
    assert usable_reply("Это работает.") == "Это работает."


def test_voice_prefers_a_filled_free_profile() -> None:
    empty_free = uuid.UUID("00000000-0000-0000-0000-000000000001")
    filled_free = uuid.UUID("00000000-0000-0000-0000-000000000002")
    filled_paused = uuid.UUID("00000000-0000-0000-0000-000000000003")
    chosen = choose_voice(
        [
            VoiceCandidate(empty_free, "Админ", ProfileContent(), free=True),
            VoiceCandidate(
                filled_free,
                "Анна",
                ProfileContent(bio="ночные прогулки", city="Казань", age=24),
                free=True,
            ),
            VoiceCandidate(filled_paused, "Борис", ProfileContent(bio="чужая анкета"), free=False),
        ]
    )
    assert chosen is not None
    assert chosen.display_name == "Анна"


def test_voice_uses_a_paused_profile_only_when_nobody_is_free() -> None:
    paused = uuid.UUID("00000000-0000-0000-0000-000000000010")
    chosen = choose_voice([VoiceCandidate(paused, "Анна", ProfileContent(bio="на месте", age=14), free=False)])
    assert chosen is not None
    assert chosen.display_name == "Анна"
    assert choose_voice([]) is None
