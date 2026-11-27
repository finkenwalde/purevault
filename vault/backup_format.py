"""Version 1 portable backup format. See docs/BACKUPS.md."""

import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.core.exceptions import ValidationError

from vault.validation import validate_entry_payload

MAGIC = b"PVBACK01"
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_ENTRIES = 2000
BUNDLE_BYTES = 76
HEADER_BYTES = len(MAGIC) + BUNDLE_BYTES + 32 + 12
WRAP_DOMAIN = b"Purevault v1 recovery key\0"
FILE_DOMAIN = b"Purevault v1 file key\0"


class InvalidBackup(Exception):
    pass


def _passphrase_bytes(passphrase):
    if not isinstance(passphrase, str) or not 1 <= len(passphrase) <= 256:
        raise InvalidBackup
    try:
        return passphrase.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidBackup from exc


def _wrapping_key(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=2**16, r=8, p=1).derive(_passphrase_bytes(passphrase))


def create_recovery_bundle(passphrase):
    private = X25519PrivateKey.generate()
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(_wrapping_key(passphrase, salt)).encrypt(
        nonce, private.private_bytes_raw(), WRAP_DOMAIN + salt,
    )
    return private.public_key().public_bytes_raw(), salt + nonce + encrypted


def _file_key(private, peer):
    shared = private.exchange(X25519PublicKey.from_public_bytes(peer))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=FILE_DOMAIN).derive(shared)


def pack_backup(payload, public_key, recovery_bundle):
    if len(payload["entries"]) > MAX_ENTRIES:
        raise InvalidBackup
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(plaintext) + HEADER_BYTES + 16 > MAX_FILE_BYTES:
        raise InvalidBackup
    ephemeral = X25519PrivateKey.generate()
    nonce = os.urandom(12)
    header = MAGIC + recovery_bundle + ephemeral.public_key().public_bytes_raw() + nonce
    return header + AESGCM(_file_key(ephemeral, public_key)).encrypt(nonce, plaintext, header)


def _validated_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {"display_timezone", "entries"}:
        raise InvalidBackup
    zone, entries = payload["display_timezone"], payload["entries"]
    if not isinstance(zone, str) or not 1 <= len(zone) <= 128:
        raise InvalidBackup
    ZoneInfo(zone)
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise InvalidBackup
    validated = []
    for row in entries:
        if not isinstance(row, dict) or set(row) != {"title", "password", "delay_seconds", "relock_policy", "relock_seconds"}:
            raise InvalidBackup
        if type(row["delay_seconds"]) is not int:
            raise InvalidBackup
        if row["relock_policy"] == "manual":
            if row["relock_seconds"] is not None:
                raise InvalidBackup
        elif type(row["relock_seconds"]) is not int:
            raise InvalidBackup
        entry = validate_entry_payload({
            "title": row["title"], "password": row["password"],
            "unlock_delay": row["delay_seconds"], "unlock_unit": "seconds",
            "relock_policy": row["relock_policy"],
            "relock_after": row["relock_seconds"], "relock_unit": "seconds",
        })
        entry["title"].encode("utf-8")
        entry["password"].encode("utf-8")
        validated.append(entry)
    return zone, validated


def unpack_backup(data, passphrase):
    if not HEADER_BYTES + 16 < len(data) <= MAX_FILE_BYTES or not data.startswith(MAGIC):
        raise InvalidBackup
    salt, wrap_nonce, wrapped = data[8:24], data[24:36], data[36:84]
    ephemeral, nonce = data[84:116], data[116:HEADER_BYTES]
    try:
        private_bytes = AESGCM(_wrapping_key(passphrase, salt)).decrypt(
            wrap_nonce, wrapped, WRAP_DOMAIN + salt,
        )
        private = X25519PrivateKey.from_private_bytes(private_bytes)
        plaintext = AESGCM(_file_key(private, ephemeral)).decrypt(nonce, data[HEADER_BYTES:], data[:HEADER_BYTES])
        return _validated_payload(json.loads(plaintext))
    except (InvalidTag, ValueError, TypeError, RecursionError, ValidationError, ZoneInfoNotFoundError) as exc:
        raise InvalidBackup from exc
