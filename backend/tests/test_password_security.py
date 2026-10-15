import asyncio
import base64
import hashlib
import threading

import pytest
from argon2 import PasswordHasher, Type

from app.core.security import hash_password, password_work, verify_and_rehash, verify_password


def legacy_hash(password="oldpass"):
    salt = b"synthetic-salt16"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2_sha256$260000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def test_argon2_passwords_are_salted_and_verify_without_silent_truncation():
    password = "a long passphrase with 中文 🔒"
    first, second = hash_password(password), hash_password(password)
    assert first != second and first.startswith("$argon2id$")
    assert verify_password(password, first)
    assert not verify_password(password + "x", first)
    assert verify_and_rehash(password, first) == (True, None)


def test_legacy_password_rehashes_only_after_correct_verification():
    encoded = legacy_hash()
    assert verify_and_rehash("wrong", encoded) == (False, None)
    valid, replacement = verify_and_rehash("oldpass", encoded)
    assert valid and replacement.startswith("$argon2id$")
    assert verify_password("oldpass", replacement)


def test_old_argon2_parameters_are_updated_on_successful_login():
    old = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1, type=Type.ID).hash("long test password")
    valid, replacement = verify_and_rehash("long test password", old)
    assert valid and replacement is not None
    assert verify_password("long test password", replacement)


@pytest.mark.parametrize("encoded", [
    "", "pbkdf2_sha256$-1$garbage$garbage", "pbkdf2_sha256$999999999999$eA==$eA==",
    "pbkdf2_sha256$260000$!!!$!!!", "pbkdf2_sha256$260000$eA==$eA==", "unknown$1$a$b",
    "$argon2id$v=19$m=999999999,t=3,p=4$c2FsdA$ZGlnaWVzdA", "$argon2id$broken", "a" * 513,
])
def test_malformed_or_unbounded_hashes_fail_closed(encoded):
    assert not verify_password("test", encoded)


def test_unknown_user_performs_dummy_verification():
    assert verify_and_rehash("unknown user password", None) == (False, None)


@pytest.mark.asyncio
async def test_password_work_does_not_block_event_loop_and_bounds_threads():
    release, lock = threading.Event(), threading.Lock()
    running = peak = 0

    def work():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(3)
        with lock:
            running -= 1
        return True

    jobs = [asyncio.create_task(password_work(work)) for _ in range(6)]
    try:
        async with asyncio.timeout(2):
            while peak < 2:
                await asyncio.sleep(0.01)
        assert peak == 2
    finally:
        release.set()
        assert all(await asyncio.gather(*jobs))
    assert peak == 2
