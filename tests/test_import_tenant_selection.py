"""Tenant selector input is rejected before a database transaction exists."""

from inspect import Parameter, signature

import pytest

from app.postgres_import_leases import PostgresImportLeaseRepository
from app.import_publication_recovery import PostgresImportPublicationRecoveryRepository


class TransactionBomb:
    def transaction(self):
        raise AssertionError("Selector opened a database transaction")


class FrozenSubclass(frozenset):
    pass


def selectors(database):
    ordinary = PostgresImportLeaseRepository(database)
    recovery = PostgresImportPublicationRecoveryRepository(database)
    return (
        (ordinary.claim_next, ("worker",), None),
        (ordinary.recover_expired, (), 0),
        (recovery.claim_next, ("worker",), None),
    )


@pytest.mark.parametrize("value", [
    set(), [], (), "user", FrozenSubclass({"user"}), frozenset({None}),
    frozenset({""}), frozenset({" "}), frozenset({" user"}),
    frozenset({"user "}), frozenset({1}), frozenset({b"user"}),
])
def test_invalid_allowlist_never_opens_transaction(value):
    for method, args, _ in selectors(TransactionBomb()):
        with pytest.raises(ValueError,
                           match="^allowed_user_ids must be a frozenset of nonempty user IDs$"):
            method(*args, allowed_user_ids=value)


def test_empty_allowlist_has_no_database_access():
    for method, args, result in selectors(TransactionBomb()):
        assert method(*args, allowed_user_ids=frozenset()) == result


def test_argument_validation_precedes_allowlist_and_database():
    ordinary = PostgresImportLeaseRepository(TransactionBomb())
    recovery = PostgresImportPublicationRecoveryRepository(TransactionBomb())
    with pytest.raises(ValueError, match="worker_id"):
        ordinary.claim_next("", allowed_user_ids=[])
    with pytest.raises(ValueError, match="limit"):
        ordinary.recover_expired(0, allowed_user_ids=[])
    with pytest.raises(ValueError, match="Recovery worker ID"):
        recovery.claim_next("", allowed_user_ids=[])


def test_selector_signatures_keep_positional_prefix_and_keyword_only_filter():
    expected = (
        (PostgresImportLeaseRepository.claim_next, ["self", "worker_id", "lease_seconds"]),
        (PostgresImportLeaseRepository.recover_expired, ["self", "limit"]),
        (PostgresImportPublicationRecoveryRepository.claim_next,
         ["self", "worker_id", "lease_seconds"]),
    )
    for method, prefix in expected:
        params = list(signature(method).parameters.values())
        assert [param.name for param in params[:-1]] == prefix
        assert params[-1].name == "allowed_user_ids"
        assert params[-1].kind == Parameter.KEYWORD_ONLY
        assert params[-1].default is None
