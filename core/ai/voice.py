import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from core.schemas.operator import ProfileContent


@dataclass(frozen=True)
class VoiceCandidate:
    operator_id: uuid.UUID
    display_name: str
    profile: ProfileContent
    free: bool


def choose_voice(candidates: Sequence[VoiceCandidate]) -> VoiceCandidate | None:
    if not candidates:
        return None
    free = [item for item in candidates if item.free]
    pool = free or list(candidates)
    return sorted(pool, key=lambda item: (-_weight(item.profile), str(item.operator_id)))[0]


def _weight(profile: ProfileContent) -> int:
    weight = 0
    if profile.bio:
        weight += 4
    if profile.city:
        weight += 2
    if profile.tags:
        weight += 1
    if profile.age is not None and profile.age >= 18:
        weight += 1
    return weight
