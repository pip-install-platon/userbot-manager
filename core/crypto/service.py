import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from core.schemas.operator import ProfileContent

_NONCE_LENGTH = 12
_TAG_LENGTH = 16
_INFO = b"operator-v1"
_DESK_SALT = b"crm-desk"
_DESK_INFO = b"desk-v1"


class CryptoService:
    """Derives a distinct AES key per operator. There is no bulk-decrypt method."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._master_key = master_key

    def encrypt_for_operator(self, operator_id: uuid.UUID, plaintext: bytes) -> bytes:
        return self._seal(self._key(operator_id), plaintext)

    def decrypt_for_operator(self, operator_id: uuid.UUID, blob: bytes) -> bytes:
        return self._open(self._key(operator_id), blob)

    def encrypt_for_desk(self, plaintext: bytes) -> bytes:
        return self._seal(self._desk_key(), plaintext)

    def decrypt_for_desk(self, blob: bytes) -> bytes:
        return self._open(self._desk_key(), blob)

    def decrypt_text(self, operator_id: uuid.UUID | None, blob: bytes) -> str:
        raw = self.decrypt_for_desk(blob) if operator_id is None else self.decrypt_for_operator(operator_id, blob)
        return raw.decode()

    def encrypt_profile(self, operator_id: uuid.UUID, profile: ProfileContent) -> bytes:
        return self.encrypt_for_operator(operator_id, profile.model_dump_json().encode())

    def decrypt_profile(self, operator_id: uuid.UUID, ciphertext: bytes) -> ProfileContent:
        return ProfileContent.model_validate_json(self.decrypt_for_operator(operator_id, ciphertext))

    def _key(self, operator_id: uuid.UUID) -> bytes:
        return self._derive(str(operator_id).encode(), _INFO)

    def _desk_key(self) -> bytes:
        return self._derive(_DESK_SALT, _DESK_INFO)

    def _derive(self, salt: bytes, info: bytes) -> bytes:
        return HKDF(algorithm=SHA256(), length=32, salt=salt, info=info).derive(self._master_key)

    @staticmethod
    def _seal(key: bytes, plaintext: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LENGTH)
        return nonce + AESGCM(key).encrypt(nonce, plaintext, None)

    @staticmethod
    def _open(key: bytes, blob: bytes) -> bytes:
        if len(blob) < _NONCE_LENGTH + _TAG_LENGTH:
            raise ValueError("ciphertext is too short")
        nonce = blob[:_NONCE_LENGTH]
        return AESGCM(key).decrypt(nonce, blob[_NONCE_LENGTH:], None)


def encrypt_for_operator(service: CryptoService, operator_id: uuid.UUID, plaintext: bytes) -> bytes:
    return service.encrypt_for_operator(operator_id, plaintext)


def decrypt_for_operator(service: CryptoService, operator_id: uuid.UUID, blob: bytes) -> bytes:
    return service.decrypt_for_operator(operator_id, blob)
