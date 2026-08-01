import unittest
import tempfile
import time
from pathlib import Path
from threading import RLock

from hello_agents.memory.base import MemoryConfig
from app.history import HistoryRepository
from assistants.pdf_learning_assistant import ImportRAGError, PDFLearningAssistant


class FakeMemoryTool:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "memory-ok"


class FakeRAGTool:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "rag-ok"


class FakeActionResult:
    def __init__(
        self,
        success,
        message="rag-ok",
        data=None,
        error="",
        error_code="",
        retryable=False,
    ):
        self.success = success
        self.message = message
        self.data = data or {}
        self.error = error
        self.error_code = error_code
        self.retryable = retryable


class FakeStructuredRAGTool(FakeRAGTool):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def execute_result(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class PDFLearningAssistantMultiDocumentTests(unittest.TestCase):
    def make_assistant(self):
        assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
        assistant.user_id = "tester"
        assistant.session_id = "session-test"
        assistant._lock = RLock()
        assistant.memory_tool = FakeMemoryTool()
        assistant.rag_tool = FakeRAGTool()
        assistant.current_document = "/docs/current.md"
        assistant.current_document_id = "current-doc"

        # Use HistoryRepository as the single persistence path
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        history_path = Path(tmpdir.name) / "history.json"
        assistant.history_repository = HistoryRepository(history_path)
        assistant.coordinator = None
        assistant.history_repository.save({
            "documents": [
                {
                    "document_id": "doc-1",
                    "document_name": "One.md",
                    "document_path": "/docs/one.md",
                },
                {
                    "document_id": "doc-2",
                    "document_name": "Two.md",
                    "document_path": "/docs/two.md",
                },
            ],
            "questions": [],
            "notes": [],
            "sessions": [],
        })
        assistant.history = assistant._load_history()

        assistant.stats = {
            "session_start": "now",
            "documents_loaded": 2,
            "questions_asked": 0,
            "notes_added": 0,
        }
        return assistant

    def test_ask_passes_selected_document_ids_and_mode_to_rag(self):
        assistant = self.make_assistant()

        answer = assistant.ask(
            "对比两篇文档",
            selected_documents=["One.md | doc-1", "Two.md | doc-2"],
            mode="compare",
        )

        self.assertEqual(answer, "rag-ok")
        args, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(args, ("ask",))
        self.assertEqual(kwargs["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(kwargs["mode"], "compare")
        self.assertNotIn("document_id", kwargs)

    def test_background_summary_task_completes_and_keeps_document_scope(self):
        assistant = self.make_assistant()
        task = assistant.start_summary_task(
            "summarize",
            ["One.md | doc-1", "Two.md | doc-2"],
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = assistant.get_summary_task(task["task_id"])
            if task["status"] == "completed":
                break
            time.sleep(0.01)
        assistant._summary_task_manager.close()

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"], "rag-ok")
        _, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(kwargs["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(kwargs["mode"], "summary")
        self.assertIn("progress_callback", kwargs)
        self.assertIn("cancel_event", kwargs)

    def test_ask_history_records_document_scope_and_mode(self):
        assistant = self.make_assistant()

        assistant.ask(
            "联合总结",
            selected_documents=["One.md | doc-1", "Two.md | doc-2"],
            mode="summary",
        )

        item = assistant.history["questions"][-1]
        self.assertEqual(item["document_ids"], ["doc-1", "doc-2"])
        self.assertEqual(item["document_names"], ["One.md", "Two.md"])
        self.assertEqual(item["mode"], "summary")
        self.assertEqual(item["document"], "One.md | doc-1; Two.md | doc-2")

    def test_ask_explicit_empty_selection_does_not_fallback_to_current_document(self):
        assistant = self.make_assistant()

        answer = assistant.ask("问题", selected_documents=[], mode="joint")

        self.assertIn("请选择", answer)
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_ask_legacy_call_still_uses_current_document_id(self):
        assistant = self.make_assistant()

        assistant.ask("普通问题")

        args, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(args, ("ask",))
        self.assertEqual(kwargs["document_id"], "current-doc")
        self.assertNotIn("document_ids", kwargs)

    def test_ask_rejects_more_than_ten_documents(self):
        assistant = self.make_assistant()
        selected = [f"Doc {index}.md | doc-{index}" for index in range(11)]

        answer = assistant.ask("问题", selected_documents=selected, mode="joint")

        self.assertIn("最多选择 10 篇", answer)
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_search_rejects_more_than_ten_documents(self):
        assistant = self.make_assistant()
        selected = [f"Doc {index}.md | doc-{index}" for index in range(11)]

        answer = assistant.search("问题", selected_documents=selected)

        self.assertIn("最多选择 10 篇", answer)
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_invalid_mode_is_rejected_before_any_side_effect(self):
        assistant = self.make_assistant()
        starting_stats = dict(assistant.stats)

        answer = assistant.ask(
            "问题",
            selected_documents=["One.md | doc-1"],
            mode="unsupported",
        )

        self.assertIn("不支持的问答模式", answer)
        self.assertEqual(assistant.stats, starting_stats)
        self.assertEqual(assistant.memory_tool.calls, [])
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_invalid_document_label_is_rejected_before_any_side_effect(self):
        assistant = self.make_assistant()
        starting_stats = dict(assistant.stats)

        answer = assistant.ask("问题", selected_documents=["Broken | "])

        self.assertIn("文档选择无效", answer)
        self.assertEqual(assistant.stats, starting_stats)
        self.assertEqual(assistant.memory_tool.calls, [])
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_auto_compare_with_one_document_is_rejected_before_side_effects(self):
        assistant = self.make_assistant()
        starting_stats = dict(assistant.stats)

        answer = assistant.ask(
            "请比较共同点",
            selected_documents=["One.md | doc-1"],
            mode="auto",
        )

        self.assertIn("至少需要选择 2 篇", answer)
        self.assertEqual(assistant.stats, starting_stats)
        self.assertEqual(assistant.memory_tool.calls, [])
        self.assertEqual(assistant.rag_tool.calls, [])

    def test_history_records_resolved_auto_mode(self):
        assistant = self.make_assistant()

        assistant.ask(
            "普通问题",
            selected_documents=["One.md | doc-1"],
            mode="auto",
        )

        self.assertEqual(assistant.history["questions"][-1]["mode"], "joint")
        _, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(kwargs["mode"], "joint")

    def test_explicit_joint_mode_is_not_overridden_by_summary_keywords(self):
        assistant = self.make_assistant()

        assistant.ask(
            "请总结这些材料",
            selected_documents=["One.md | doc-1", "Two.md | doc-2"],
            mode="joint",
        )

        _, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(kwargs["mode"], "joint")
        self.assertNotIn("summary_mode", kwargs)

    def test_search_passes_selected_document_ids_to_rag(self):
        assistant = self.make_assistant()

        result = assistant.search(
            "alpha",
            selected_documents=["Two.md | doc-2", "One.md | doc-1"],
        )

        self.assertEqual(result, "rag-ok")
        args, kwargs = assistant.rag_tool.calls[-1]
        self.assertEqual(args, ("search",))
        self.assertEqual(kwargs["document_ids"], ["doc-2", "doc-1"])
        self.assertNotIn("document_id", kwargs)

    def test_constructor_uses_runtime_dir_for_writable_state(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        runtime_dir = Path(tmpdir.name)

        assistant = PDFLearningAssistant(user_id="runtime-user", runtime_dir=runtime_dir)
        self.addCleanup(assistant.close)

        self.assertEqual(
            assistant.history_path,
            runtime_dir / "memory" / "learning_history_runtime-user.json",
        )
        self.assertEqual(
            assistant.memory_tool.memory_config.database_path,
            str(runtime_dir / "memory" / "memory_runtime-user.db"),
        )
        self.assertEqual(
            assistant.rag_tool._pipelines["pdf_runtime-user"].cache_path,
            runtime_dir / "rag" / "rag_cache.json",
        )

    def test_load_document_updates_state_only_on_structured_success(self):
        assistant = self.make_assistant()
        assistant.rag_tool = FakeStructuredRAGTool(
            FakeActionResult(
                True,
                "loaded",
                {"document_id": "new-doc", "chunks_added": 1},
            )
        )
        path = Path(tempfile.mkdtemp()) / "new-doc.md"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        path.write_text("body", encoding="utf-8")

        result = assistant.load_document(str(path))

        self.assertEqual(result, "loaded")
        self.assertEqual(assistant.current_document_id, "new-doc")
        self.assertEqual(assistant.stats["documents_loaded"], 3)
        self.assertEqual(assistant.history["documents"][-1]["document_id"], "new-doc")

    def test_load_document_does_not_update_state_on_structured_failure(self):
        assistant = self.make_assistant()
        assistant.rag_tool = FakeStructuredRAGTool(
            FakeActionResult(False, "import failed", {"document_id": "failed-doc"})
        )
        path = Path(tempfile.mkdtemp()) / "failed-doc.md"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        path.write_text("body", encoding="utf-8")

        with self.assertRaises(ImportRAGError) as captured:
            assistant.load_document(str(path))

        self.assertEqual(captured.exception.error_code, "rag_operation")
        self.assertFalse(captured.exception.retryable)
        self.assertEqual(assistant.current_document_id, "current-doc")
        self.assertEqual(assistant.current_document, "/docs/current.md")
        self.assertEqual(assistant.stats["documents_loaded"], 2)
        self.assertEqual([item["document_id"] for item in assistant.history["documents"]], ["doc-1", "doc-2"])


if __name__ == "__main__":
    unittest.main()
