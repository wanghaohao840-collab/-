from typing import get_args

import pytest

import app.import_models as import_models
from app.import_models import ImportLimits, validate_batch_sizes


def test_cancellation_model_contract_is_exposed():
    assert "cancelled" in get_args(import_models.ImportStatus)
    assert "cancelled" in get_args(import_models.ImportStage)
    assert get_args(import_models.CancelOutcome) == (
        "cancelled",
        "cancel_requested",
        "not_cancellable",
        "unchanged",
    )
    assert "cancel_requested_at" in import_models.ImportTaskRecord.__dataclass_fields__
    assert "cancelled" in import_models.ImportBatchSummary.__dataclass_fields__
    assert tuple(import_models.ImportCancelDecision.__dataclass_fields__) == (
        "task",
        "outcome",
    )


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
