import uuid

import pytest
from cryptography.exceptions import InvalidTag

from core.crypto.service import CryptoService, decrypt_for_operator, encrypt_for_operator
from core.schemas.operator import ProfileContent


def test_roundtrip_and_operator_isolation() -> None:
    service = CryptoService(b"m" * 32)
    owner = uuid.uuid4()
    stranger = uuid.uuid4()
    blob = encrypt_for_operator(service, owner, "секрет клиента".encode())

    assert decrypt_for_operator(service, owner, blob) == "секрет клиента".encode()
    with pytest.raises(InvalidTag):
        service.decrypt_for_operator(stranger, blob)


def test_tampered_blob_and_short_blob_are_rejected() -> None:
    service = CryptoService(b"m" * 32)
    operator_id = uuid.uuid4()
    blob = bytearray(service.encrypt_for_operator(operator_id, b"payload"))
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        service.decrypt_for_operator(operator_id, bytes(blob))
    with pytest.raises(ValueError):
        service.decrypt_for_operator(operator_id, b"short")


def test_profile_roundtrip_uses_the_operator_id() -> None:
    service = CryptoService(b"m" * 32)
    operator_id = uuid.uuid4()
    profile = ProfileContent(bio="только этот оператор", age=30, city="Казань", tags=["ночь"])
    blob = service.encrypt_profile(operator_id, profile)
    assert service.decrypt_profile(operator_id, blob) == profile


def test_there_is_no_bulk_decrypt() -> None:
    assert not hasattr(CryptoService, "decrypt_all")
    assert "decrypt_all" not in vars(CryptoService)
