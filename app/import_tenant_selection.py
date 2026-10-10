"""Strict input boundary for optional PostgreSQL import tenant selectors."""


def validate_allowed_user_ids(value: frozenset[str] | None) -> frozenset[str] | None:
    if value is None:
        return None
    if (type(value) is not frozenset or any(
            type(user_id) is not str or not user_id or user_id != user_id.strip()
            for user_id in value)):
        raise ValueError('allowed_user_ids must be a frozenset of nonempty user IDs')
    return value
