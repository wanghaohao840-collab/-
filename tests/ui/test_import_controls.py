from __future__ import annotations


def _bindings(module, function):
    return [block_fn for block_fn in module.demo.fns.values() if block_fn.fn is function]


def test_task_control_bindings_use_only_session_visible_batch_and_hidden_selection():
    import ui.gradio_app as module

    expected = {
        module.pause_import_task: 3,
        module.resume_import_task: 3,
        module.cancel_import_task: 4,
    }
    for handler, input_count in expected.items():
        bindings = _bindings(module, handler)
        assert len(bindings) == 1
        binding = bindings[0]
        assert len(binding.inputs) == input_count
        assert len(binding.outputs) == 3
        assert binding.inputs[0].__class__.__name__ == "State"
        assert binding.inputs[1].__class__.__name__ == "Dropdown"
        assert binding.inputs[2].__class__.__name__ == "State"
        assert binding.outputs[-1].__class__.__name__ == "State"


def test_batch_control_bindings_use_visible_batch_and_cancel_confirmation():
    import ui.gradio_app as module

    expected = {
        module.pause_import_batch: 2,
        module.resume_import_batch: 2,
        module.cancel_import_batch: 3,
    }
    for handler, input_count in expected.items():
        bindings = _bindings(module, handler)
        assert len(bindings) == 1
        binding = bindings[0]
        assert len(binding.inputs) == input_count
        assert len(binding.outputs) == 3
        assert binding.inputs[0].__class__.__name__ == "State"
        assert binding.inputs[1].__class__.__name__ == "Dropdown"
        if handler is module.cancel_import_batch:
            assert binding.inputs[-1].__class__.__name__ == "Checkbox"


def test_timer_is_read_only_and_never_refreshes_history_or_controls():
    import ui.gradio_app as module

    binding = next(
        block_fn
        for block_fn in _bindings(module, module.refresh_import_batch)
        if getattr(block_fn, "queue", None) is False
    )

    assert len(binding.inputs) == 3
    assert len(binding.outputs) == 3
    assert binding.outputs[-1].__class__.__name__ == "State"


def test_login_refreshes_active_imports_and_logout_clears_task_and_confirmation_state():
    import ui.gradio_app as module

    refreshes = _bindings(module, module.refresh_import_batches)
    assert len(refreshes) >= 3
    clear = _bindings(module, module.clear_import_ui)
    assert len(clear) == 1
    assert len(clear[0].outputs) == 5
    assert clear[0].outputs[-2].__class__.__name__ == "State"
    assert clear[0].outputs[-1].__class__.__name__ == "Checkbox"
    assert module.clear_import_ui()[-2:] == ("", False)


def test_batch_change_and_polling_clear_selection_when_task_loses_every_action():
    import ui.gradio_app as module
    from app.import_models import ImportBatchSummary
    from tests.ui.test_import_handlers import _task

    summary = ImportBatchSummary(
        batch_id="batch-a", user_id="private", created_at="", updated_at="",
        total=1, queued=0, running=0, retry_wait=0, succeeded=1, failed=0,
        tasks=(_task(status="succeeded", stage="succeeded"),),
    )

    assert module._retain_import_task_selection(
        summary, "batch-a", ("batch-a", "task-a")
    ) == ""
