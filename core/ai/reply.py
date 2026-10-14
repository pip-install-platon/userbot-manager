import re

_MAX_CHARS = 180
_MAX_SENTENCES = 2
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_LEAK = re.compile(
    r"на смен|никто не|все отдыха|оператор|уточню у|кто свобод",
    re.IGNORECASE,
)


def tighten(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    sentences = [part for part in _SENTENCE_SPLIT.split(cleaned) if part]
    kept = " ".join(sentences[:_MAX_SENTENCES]) if sentences else cleaned
    if len(kept) <= _MAX_CHARS:
        return kept
    clipped = kept[:_MAX_CHARS]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    shortened = clipped.rstrip(".,;:!-— ")
    return shortened or kept[:_MAX_CHARS]


def usable_reply(text: str) -> str | None:
    cleaned = tighten(text)
    if not cleaned or _LEAK.search(cleaned):
        return None
    return cleaned
