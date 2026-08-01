import time
from threading import Event

from app.summary_tasks import SummaryTaskManager


def wait_for_status(manager, task_id, expected, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task["status"] in expected:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {expected}: {manager.get(task_id)}")


def test_summary_task_reports_progress_and_result():
    manager = SummaryTaskManager(max_workers=1)

    def runner(progress, cancel_event):
        progress(completed=1, total=2, stage="mapping", document_id="doc-1")
        progress(completed=2, total=2, stage="reducing")
        return "combined summary"

    try:
        task = manager.start(runner, total=2)
        completed = wait_for_status(
            manager,
            task["task_id"],
            {"completed"},
        )
    finally:
        manager.close()

    assert completed["completed"] == 2
    assert completed["stage"] == "completed"
    assert completed["result"] == "combined summary"


def test_summary_task_can_be_cancelled():
    manager = SummaryTaskManager(max_workers=1)
    started = Event()
    release = Event()

    def runner(progress, cancel_event):
        started.set()
        release.wait(timeout=2)
        return "ignored"

    try:
        task = manager.start(runner, total=3)
        assert started.wait(timeout=1)
        cancelled = manager.cancel(task["task_id"])
        release.set()
        final = wait_for_status(
            manager,
            task["task_id"],
            {"cancelled"},
        )
    finally:
        release.set()
        manager.close()

    assert cancelled["status"] == "cancelled"
    assert final["result"] is None
