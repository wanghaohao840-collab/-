from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.auth import Credentials


def test_credentials_schema_matches_domain_length_contracts():
    properties = Credentials.model_json_schema()["properties"]

    assert properties["username"]["minLength"] == 3
    assert properties["username"]["maxLength"] == 32
    assert properties["password"]["minLength"] == 8
    assert properties["password"]["maxLength"] == 128


def test_credentials_accept_domain_upper_length_boundaries():
    credentials = Credentials(username="a" * 32, password="p" * 128)

    assert credentials.username == "a" * 32
    assert credentials.password.get_secret_value() == "p" * 128


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"username": "a" * 33, "password": "p" * 8}, "username"),
        ({"username": "reader", "password": "p" * 129}, "password"),
    ],
)
def test_credentials_reject_values_above_domain_length_boundaries(payload, field):
    with pytest.raises(ValidationError) as exc_info:
        Credentials.model_validate(payload)

    assert exc_info.value.errors()[0]["loc"] == (field,)
