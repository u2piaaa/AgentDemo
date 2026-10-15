import base64
import binascii
import hashlib
import hmac
import secrets
from functools import partial

import anyio
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError


PASSWORD_ITERATIONS = 260_000
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)
PASSWORD_WORKERS = anyio.CapacityLimiter(2)
# Unknown users still perform one normal hash verification.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not isinstance(password_hash, str) or len(password_hash) > 512:
        return False
    if password_hash.startswith("$argon2"):
        try:
            parameters = extract_parameters(password_hash)
            if not (parameters.type == Type.ID and parameters.version == 19
                    and 1 <= parameters.time_cost <= 10
                    and 8 <= parameters.memory_cost <= 262144
                    and 1 <= parameters.parallelism <= 16
                    and 8 <= parameters.salt_len <= 64 and 16 <= parameters.hash_len <= 64):
                return False
            return PASSWORD_HASHER.verify(password_hash, password)
        except (InvalidHashError, VerificationError, ValueError):
            return False
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if not 1 <= iterations <= 2_000_000:
            return False
        salt = base64.b64decode(salt_text, altchars=b"-_", validate=True)
        expected = base64.b64decode(digest_text, altchars=b"-_", validate=True)
        if not 8 <= len(salt) <= 64 or len(expected) != 32:
            return False
    except (ValueError, TypeError, binascii.Error):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def verify_and_rehash(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    if password_hash is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return False, None
    if not verify_password(password, password_hash):
        return False, None
    if password_hash.startswith("pbkdf2_sha256$") or PASSWORD_HASHER.check_needs_rehash(password_hash):
        return True, hash_password(password)
    return True, None


async def password_work(function, *args):
    # Keep memory-hard work off the event loop and bound concurrent memory use.
    # Non-abandoning threads retain their limiter slot even if the client leaves.
    return await anyio.to_thread.run_sync(partial(function, *args), limiter=PASSWORD_WORKERS)


def secrets_equal(supplied: str | None, expected: str) -> bool:
    return hmac.compare_digest((supplied or "").encode(), expected.encode())


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
