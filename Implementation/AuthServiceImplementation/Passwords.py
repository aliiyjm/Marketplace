"""Password hashing with Argon2id (winner of the Password Hashing Competition,
first choice of the OWASP Password Storage Cheat Sheet).

- New passwords: Argon2id with a random salt per password ($argon2id$...).
- Older accounts were hashed with werkzeug scrypt/pbkdf2: they still sign in,
  and the hash is replaced by Argon2id right after a successful sign-in
  (the only moment the plain password is available). Nobody is locked out.
- A plain password is never stored, logged or returned.
"""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from werkzeug.security import check_password_hash

ARGON2_PREFIX = "$argon2id$"
LEGACY_PREFIXES = ("scrypt:", "pbkdf2:")
# argon2-cffi defaults (RFC 9106 low-memory profile): 3 iterations, 64 MiB, 4 lanes.
_hasher = PasswordHasher()
_DUMMY = None


def hash_password(password):
    """New Argon2id hash of `password`."""
    return _hasher.hash(password)


def verify_password(stored_hash, password):
    """True when `password` matches `stored_hash` (Argon2id or a legacy werkzeug hash)."""
    stored_hash = str(stored_hash or "")
    if stored_hash.startswith(ARGON2_PREFIX):
        try:
            return _hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    if stored_hash.startswith(LEGACY_PREFIXES):
        return check_password_hash(stored_hash, password)
    return False                       # "!..." = account that cannot sign in


def needs_rehash(stored_hash):
    """True for legacy hashes and Argon2 hashes made with weaker settings."""
    stored_hash = str(stored_hash or "")
    if not stored_hash.startswith(ARGON2_PREFIX):
        return True
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def burn_time(password):
    """Spend the same time as a real check (unknown email): response time does
    not reveal which emails exist."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("not-a-real-password")
    verify_password(_DUMMY, password)
