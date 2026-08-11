from dataclasses import fields
from typing import get_args

import pytest

from app.import_models import (
    BatchLifecycleState,
    ImportBatchSummary,
    ImportHistoryFilters,
    ImportHistoryPage,
    ImportLimits,
    ImportStage,
    ImportStatus,
    ImportTaskEventRecord,
    ImportTaskRecord,
    validate_batch_sizes,
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


def test_import_control_model_shapes_are_available():
    assert get_args(ImportStatus) == (
        "queued",
        "running",
        "retry_wait",
        "pause_requested",
        "paused",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
    )
    assert get_args(BatchLifecycleState) == ("active", "deleting")
    assert {"paused", "cancelled"} <= set(get_args(ImportStage))
    assert {"control_requested_at", "control_claimed_at"} <= {
        field.name for field in fields(ImportTaskRecord)
    }
    assert {"lifecycle_state", "paused", "pause_requested", "cancel_requested", "cancelled"} <= {
        field.name for field in fields(ImportBatchSummary)
    }
    assert [field.name for field in fields(ImportTaskEventRecord)] == [
        "event_id",
        "batch_id",
        "task_id",
        "user_id",
        "event_type",
        "status",
        "stage",
        "message",
        "created_at",
    ]
    assert ImportHistoryFilters().statuses == ()
    assert ImportHistoryFilters().filename_query == ""
    assert [field.name for field in fields(ImportHistoryPage)] == [
        "batches",
        "next_cursor",
    ]
