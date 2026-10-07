import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

def _derive_key(master_password: str, salt_hex: str) -> bytes:
    return hashlib.scrypt(
        master_password.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=64 * 1024 * 1024,
        dklen=32,
    )

def _fernet_key(master_password: str, salt_hex: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive_key(master_password, salt_hex)))

def wrap_vault_key(master_password: str, salt_hex: str, secret_key: str) -> str:
    """Keep the derived vault key in the server-side session without storing it in plaintext."""
    wrapping_key = hashlib.sha256(b"PureVault session key\0" + secret_key.encode("utf-8")).digest()
    wrapper = Fernet(base64.urlsafe_b64encode(wrapping_key))
    return wrapper.encrypt(_derive_key(master_password, salt_hex)).decode("ascii")

def unwrap_vault_key(wrapped_key: str, secret_key: str) -> bytes:
    wrapping_key = hashlib.sha256(b"PureVault session key\0" + secret_key.encode("utf-8")).digest()
    wrapper = Fernet(base64.urlsafe_b64encode(wrapping_key))
    try:
        return wrapper.decrypt(wrapped_key.encode("ascii"))
    except (InvalidToken, ValueError, UnicodeEncodeError):
        raise ValueError("The session encryption key is unavailable. Log in again.")

def _fernet_from_key(key: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(key))

def encrypt_secret_with_key(key: bytes, secret: str) -> str:
    return _fernet_from_key(key).encrypt(secret.encode("utf-8")).decode("ascii")

def encrypt_secret(master_password: str, salt_hex: str, secret: str) -> str:
    return encrypt_secret_with_key(_derive_key(master_password, salt_hex), secret)

def decrypt_secret(master_password: str, salt_hex: str, ciphertext: str) -> str:
    try:
        return _fernet_key(master_password, salt_hex).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        raise ValueError("That master password is incorrect or the stored value cannot be decrypted.")
