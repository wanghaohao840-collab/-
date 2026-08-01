import pytest

from app.import_models import ImportLimits, validate_batch_sizes


def test_validate_batch_sizes_accepts_20_files_and_500_mib():
    limits = ImportLimits()

    validate_batch_sizes([100 * 1024 * 1024] * 5, limits)


def test_validate_batch_sizes_rejects_21_files():
    with pytest.raises(ValueError, match="20"):
        validate_batch_sizes([1] * 21, ImportLimits())


@pytest.mark.parametrize("sizes", [[], [-1], [100 * 1024 * 1024 + 1]])
def test_validate_batch_sizes_rejects_invalid_file_sizes(sizes):
    with pytest.raises(ValueError):
        validate_batch_sizes(sizes, ImportLimits())
