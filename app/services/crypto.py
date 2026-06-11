from cryptography.fernet import Fernet
import base64
import hashlib
import logging

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

logger = logging.getLogger(__name__)


def _get_fernet_key() -> bytes:
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    f = Fernet(_get_fernet_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ciphertext
    f = Fernet(_get_fernet_key())
    return f.decrypt(ciphertext.encode()).decode()


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return decrypt(value)
        except Exception:
            return value
