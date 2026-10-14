import math
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class DialogExample:
    client: str
    reply: str
    hints: tuple[str, ...] = ()


EXAMPLES: tuple[DialogExample, ...] = (
    DialogExample("привет", "Привет. Ты из какого города?", ("здравствуй", "хай", "ку", "hello")),
    DialogExample("[стикер]", "Привет. Напиши город и во сколько удобно.", ("стикер", "гиф")),
    DialogExample("[голосовое]", "Голос не разбираю. Напиши город и время текстом.", ("голосовое", "аудио")),
    DialogExample("[файл]", "Файл не открою. Напиши текстом.", ("документ", "файл")),
    DialogExample("я из казани", "Казань, поняла. Во сколько удобно?", ("город", "казань")),
    DialogExample("вечером", "Вечером. А город какой?", ("утром", "днём", "ночью", "завтра", "сегодня")),
    DialogExample("казань в 19:00", "Записала: Казань, примерно 19:00.", ("время", "часов")),
    DialogExample("я в москве часов в 8", "Записала: Москва, примерно в 8.", ("питер", "спб")),
    DialogExample("можно завтра днём", "Завтра днём. Город напиши.", ("послезавтра",)),
    DialogExample("да", "Договорились.", ("ок", "хорошо", "подтверждаю", "давай")),
    DialogExample("скинь фото", "Фото пришлю отдельно.", ("фотку", "фотографию", "картинку")),
    DialogExample("скинь кружок", "Кружок пришлю отдельно.", ("кружочек", "видео")),
    DialogExample("ты кто", "Я на связи. Ты из какого города?", ("бот", "человек", "настоящая")),
    DialogExample("сколько стоит", "Сначала город и время.", ("цена", "прайс", "дорого")),
    DialogExample("как дела", "Нормально. Ты из какого города?", ("что делаешь", "как ты")),
    DialogExample("давай перенесём", "Напиши другой день и примерно во сколько.", ("отмени", "не смогу")),
)

_EXAMPLE_LIMIT = 3
_DOCUMENTS = tuple(
    set(_TOKEN.findall(f"{example.client} {' '.join(example.hints)}".casefold().replace("ё", "е")))
    for example in EXAMPLES
)
_DOCUMENT_COUNT = len(EXAMPLES)
_DOCUMENT_FREQUENCY: dict[str, int] = {}
for _document in _DOCUMENTS:
    for _token in _document:
        _DOCUMENT_FREQUENCY[_token] = _DOCUMENT_FREQUENCY.get(_token, 0) + 1


def retrieve_examples(query: str, *, limit: int = _EXAMPLE_LIMIT) -> list[DialogExample]:
    size = limit if limit > 0 else _EXAMPLE_LIMIT
    tokens = _tokens(query)
    if not tokens:
        return list(EXAMPLES[:size])
    ranked = sorted(
        ((_score(tokens, index), index) for index in range(len(EXAMPLES))),
        key=lambda item: (-item[0], item[1]),
    )
    picked = [EXAMPLES[index] for score, index in ranked if score > 0][:size]
    if picked:
        return picked
    return list(EXAMPLES[:size])


def _tokens(text: str) -> list[str]:
    folded = text.casefold().replace("ё", "е")
    return _TOKEN.findall(folded)


def _score(query_tokens: list[str], index: int) -> float:
    document = _DOCUMENTS[index]
    score = 0.0
    for token in query_tokens:
        if token in document:
            score += _idf(token)
    client_tokens = _tokens(EXAMPLES[index].client)
    if client_tokens and all(token in query_tokens for token in client_tokens):
        score += 2.0
    return score


def _idf(token: str) -> float:
    return math.log((_DOCUMENT_COUNT + 1) / (_DOCUMENT_FREQUENCY.get(token, 0) + 1)) + 1.0
