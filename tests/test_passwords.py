"""Argon2id password hashing and the automatic upgrade of older scrypt hashes."""
import pytest
from werkzeug.security import generate_password_hash

from Implementation.AuthServiceImplementation.Passwords import hash_password, needs_rehash, verify_password
from Interfaces.UserServiceInterface.UserItem import UserItem
from Interfaces.Validators import ValidationError
from tests.conftest import ANA, PASSWORD


def test_new_hashes_are_argon2id_with_a_random_salt():
    first, second = hash_password("Demo1234"), hash_password("Demo1234")
    assert first.startswith("$argon2id$") and first != second            # different salt every time
    assert verify_password(first, "Demo1234") and not verify_password(first, "demo1234")
    assert "Demo1234" not in first and not needs_rehash(first)


def test_older_scrypt_hashes_still_work_but_need_rehash():
    legacy = generate_password_hash("Demo1234", method="scrypt")
    assert verify_password(legacy, "Demo1234") and not verify_password(legacy, "nope")
    assert needs_rehash(legacy)


@pytest.mark.parametrize("stored", ["", "!abc123", "Demo1234", "$argon2id$broken", None])
def test_unusable_hashes_never_sign_in(stored):
    assert not verify_password(stored, "Demo1234")


def test_sign_in_upgrades_a_scrypt_hash_to_argon2id(services):
    services.store.users[ANA]["password_hash"] = generate_password_hash(PASSWORD, method="scrypt")
    user, _ = services.auth.sign_in({"email": "ana@example.com", "password": PASSWORD})
    assert user.id_user == ANA
    upgraded = services.store.users[ANA]["password_hash"]
    assert upgraded.startswith("$argon2id$") and verify_password(upgraded, PASSWORD)
    services.auth.sign_in({"email": "ana@example.com", "password": PASSWORD})   # still signs in after


def test_a_failed_upgrade_does_not_block_the_sign_in(services, monkeypatch):
    services.store.users[ANA]["password_hash"] = generate_password_hash(PASSWORD, method="scrypt")

    def down(*args):
        raise ValidationError("DynamoDB is down")
    monkeypatch.setattr(services.auth.impl.users, "set_password_hash", down)
    user, _ = services.auth.sign_in({"email": "ana@example.com", "password": PASSWORD})
    assert user.id_user == ANA and services.store.users[ANA]["password_hash"].startswith("scrypt:")


def test_wrong_password_does_not_change_the_hash(services):
    before = services.store.users[ANA]["password_hash"] = generate_password_hash(PASSWORD, method="scrypt")
    with pytest.raises(ValidationError):
        services.auth.sign_in({"email": "ana@example.com", "password": "Wrong1234"})
    assert services.store.users[ANA]["password_hash"] == before


def test_the_lambda_accepts_argon2_and_rejects_plain_passwords():
    base = {"id_user": "a1b2c3d4e5", "name": "Ana", "email": "ana@example.com", "role": "buyer"}
    assert UserItem.from_payload(dict(base, password_hash=hash_password("x"))).password_hash.startswith("$argon2id$")
    with pytest.raises(ValidationError):
        UserItem.from_payload(dict(base, password_hash="Demo1234"))
