import pytest

from app.qa_observability import InProcessQaTelemetry


def test_telemetry_counts_only_allowlisted_content_free_events() -> None:
    telemetry = InProcessQaTelemetry()
    telemetry.record(
        "qa_ask_success",
        duration_ms=12.5,
        conversation_id="conversation",
        message_id="message",
    )
    telemetry.record("qa_ask_success")
    assert telemetry.snapshot() == {"qa_ask_success": 2}

    with pytest.raises(ValueError):
        telemetry.record("question=secret")


def test_telemetry_snapshot_is_an_immutable_copy() -> None:
    telemetry = InProcessQaTelemetry()
    telemetry.record("qa_retry")
    snapshot = telemetry.snapshot()
    snapshot["qa_retry"] = 99
    assert telemetry.snapshot()["qa_retry"] == 1
