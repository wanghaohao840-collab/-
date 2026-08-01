import sqlite3

import pytest

from app.auth import AuthError, AuthService, normalize_username
from app.database import initialize_database


def test_register_creates_user_without_plaintext_password(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)

    user = auth.register("Alice_01", "correct horse battery")

    assert user.username == "Alice_01"
    assert user.id

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select username_key, password_hash from users where id = ?", (user.id,)
        ).fetchone()

    assert row[0] == "alice_01"
    assert "correct horse battery" not in row[1]
    assert row[1].startswith("scrypt$")


def test_duplicate_username_is_case_insensitive(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    auth.register("Alice", "correct horse battery")

    with pytest.raises(AuthError, match="Username already exists"):
        auth.register("alice", "another good password")


def test_authenticate_accepts_password_and_rejects_wrong_password(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    registered = auth.register("Bob", "correct horse battery")

    assert auth.authenticate("bob", "correct horse battery").id == registered.id

    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("bob", "wrong password")


@pytest.mark.parametrize("username", ["ab", ".alice", "alice!", "alice_"])
def test_invalid_usernames_are_rejected(tmp_path, username):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)

    with pytest.raises(AuthError):
        auth.register(username, "correct horse battery")


def test_username_normalization_uses_nfkc_and_casefold():
    display, key = normalize_username("  Alice.01  ")

    assert display == "Alice.01"
    assert key == "alice.01"
