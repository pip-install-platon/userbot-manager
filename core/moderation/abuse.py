import re

_TOKEN = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
_REPEAT = re.compile(r"(.)\1+")
_CONTAINS = ("хуй", "хуя", "хуе", "хуи", "пизд")
_PREFIXES = (
    "еб",
    "заеб",
    "выеб",
    "уеб",
    "наеб",
    "доеб",
    "проеб",
    "минет",
    "дроч",
    "бляд",
    "отсос",
    "порно",
    "fuck",
    "porn",
)
_EXACT = frozenset({"соси", "отсоси", "cunt", "whore", "dick", "cock", "slut"})


def is_abuse(text: str) -> bool:
    folded = _REPEAT.sub(r"\1", text.casefold().replace("ё", "е"))
    for token in _TOKEN.findall(folded):
        if token in _EXACT or any(stem in token for stem in _CONTAINS) or token.startswith(_PREFIXES):
            return True
    return False
