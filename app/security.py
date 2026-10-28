import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = ("VIEWER", "EDITOR", "EDITOR_PLUS", "SUPER_ADMIN")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=32,
        )
        return hmac.compare_digest(derived, bytes.fromhex(digest_hex))
    except (TypeError, ValueError):
        return False


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, supplied: str) -> bool:
    if not expected or not supplied or len(expected) != len(supplied):
        return False
    return hmac.compare_digest(expected, supplied)


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 320:
        raise ValueError("Enter a valid email address.")
    return email


def validate_password(password: str, email: str) -> None:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 128:
        raise ValueError("Password must be 12 to 128 characters.")
    if password.lower() == email.lower():
        raise ValueError("Password must not match the email address.")


def client_ip(request) -> str:
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def expires_in(**kwargs) -> datetime:
    return utcnow() + timedelta(**kwargs)
