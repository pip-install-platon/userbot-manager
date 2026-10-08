import re
from typing import assert_never

from core.constants import MediaKind

_REQUEST_LIMIT = 160

# Circles are checked before plain video: "видео кружок" is a video note.
_PATTERNS: tuple[tuple[MediaKind, re.Pattern[str]], ...] = (
    (MediaKind.VIDEO_NOTE, re.compile(r"круж", re.IGNORECASE)),
    (MediaKind.PHOTO, re.compile(r"фот(?:о|к|огр)|картинк", re.IGNORECASE)),
    (MediaKind.VIDEO, re.compile(r"видео", re.IGNORECASE)),
)

def detect_album_request(text: str | None) -> MediaKind | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > _REQUEST_LIMIT:
        return None
    for kind, pattern in _PATTERNS:
        if pattern.search(stripped):
            return kind
    return None


def missing_album_text(display_name: str, kind: MediaKind) -> str:
    match kind:
        case MediaKind.PHOTO:
            noun = "фото"
        case MediaKind.VIDEO:
            noun = "видео"
        case MediaKind.VIDEO_NOTE:
            noun = "кружков"
        case _ as unreachable:
            assert_never(unreachable)
    return f"В альбоме {display_name} пока нет {noun}."
