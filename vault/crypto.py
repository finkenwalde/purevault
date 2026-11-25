import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _load_key() -> bytes:
    path = Path(settings.PUREVAULT_KEY_FILE)
    if not path.exists():
        from vault.models import EmailSettings, MailDelivery, VaultEntry
        if VaultEntry.objects.exists() or EmailSettings.objects.filter(encrypted_password__isnull=False).exists() or MailDelivery.objects.exists():
            raise ImproperlyConfigured("The encryption key is missing while encrypted Purevault data exists. Restore the original key; a replacement will not be generated.")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
        except FileExistsError:
            pass
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("The configured Purevault encryption key is invalid.") from exc
    return key


def encrypt_secret(secret: str) -> bytes:
    return encrypt_text(secret)


def decrypt_secret(ciphertext: bytes) -> str:
    return decrypt_text(ciphertext)


def encrypt_text(value: str) -> bytes:
    return Fernet(_load_key()).encrypt(value.encode("utf-8"))


def decrypt_text(ciphertext: bytes) -> str:
    try:
        return Fernet(_load_key()).decrypt(bytes(ciphertext)).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ImproperlyConfigured("Vault data could not be decrypted with the configured key.") from exc

