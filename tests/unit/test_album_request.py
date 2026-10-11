from core.constants import MediaKind
from core.routing.album_request import detect_album_request, missing_album_text


def test_detects_photo_video_and_circle() -> None:
    assert detect_album_request("скинь фото") is MediaKind.PHOTO
    assert detect_album_request("можно фотку?") is MediaKind.PHOTO
    assert detect_album_request("видео") is MediaKind.VIDEO
    assert detect_album_request("кружочек") is MediaKind.VIDEO_NOTE
    assert detect_album_request("видео кружок") is MediaKind.VIDEO_NOTE


def test_ignores_ordinary_and_long_text() -> None:
    assert detect_album_request("Здравствуйте, нужна помощь") is None
    assert detect_album_request(None) is None
    assert detect_album_request("фото " * 40) is None


def test_missing_album_names_the_operator() -> None:
    assert missing_album_text("Питон", MediaKind.PHOTO) == "В альбоме Питон пока нет фото."
    assert missing_album_text("Питон", MediaKind.VIDEO_NOTE) == "В альбоме Питон пока нет кружков."
