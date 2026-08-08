import json
import sys
import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import gradio as gr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistants.document_selection import primary_document_label
from app.bootstrap import ApplicationServices, get_application_services
from app.session import InvalidSessionError
from app.import_models import ImportBatchSummary
from hello_agents.memory.rag.errors import sanitize_error_message
from ui.launch_config import load_launch_config


services = None
session_registry = None
legacy_migration = None
import_repository = None
import_worker_pool = None
import_service = None


@dataclass(frozen=True)
class _HandlerBindings:
    services: ApplicationServices
    session_registry: Any
    legacy_migration: Any
    import_repository: Any
    import_worker_pool: Any
    import_service: Any

    @classmethod
    def from_services(cls, services: ApplicationServices) -> "_HandlerBindings":
        return cls(
            services=services,
            session_registry=services.session_registry,
            legacy_migration=services.legacy_migration,
            import_repository=services.import_repository,
            import_worker_pool=services.import_worker_pool,
            import_service=services.import_service,
        )


_handler_bindings: ContextVar[_HandlerBindings | None] = ContextVar(
    "gradio_handler_bindings",
    default=None,
)


def _bind_app_services(application_services: ApplicationServices) -> None:
    global services, session_registry, legacy_migration, import_repository
    global import_worker_pool, import_service
    services = application_services
    session_registry = services.session_registry
    legacy_migration = services.legacy_migration
    import_repository = services.import_repository
    import_worker_pool = services.import_worker_pool
    import_service = services.import_service


def initialize_app_services() -> ApplicationServices:
    """Create persistent services for the supported script entry point."""

    application_services = get_application_services()
    _bind_app_services(application_services)
    return application_services


def _current_session_registry():
    bindings = _handler_bindings.get()
    if bindings is not None:
        return bindings.session_registry
    return session_registry


def _current_import_service():
    bindings = _handler_bindings.get()
    if bindings is not None:
        return bindings.import_service
    return import_service


def _current_legacy_migration():
    bindings = _handler_bindings.get()
    if bindings is not None:
        return bindings.legacy_migration
    return legacy_migration


def _bind_handler(
    handler: Callable,
    service_provider: Callable[[], ApplicationServices],
) -> Callable:
    """Bind one Gradio event handler to its owning application services."""

    @wraps(handler)
    def bound_handler(*args, **kwargs):
        bindings = _HandlerBindings.from_services(service_provider())
        token = _handler_bindings.set(bindings)
        try:
            return handler(*args, **kwargs)
        finally:
            _handler_bindings.reset(token)

    return bound_handler


def _require_assistant(session_token):
    if not session_token:
        raise gr.Error("Please log in first")
    registry = _current_session_registry()
    if registry is None:
        raise gr.Error(
            "Please log in again; application services are not initialized"
        )
    try:
        return registry.get_assistant(session_token)
    except InvalidSessionError as exc:
        raise gr.Error(str(exc))


def _require_session(session_token):
    if not session_token:
        raise gr.Error("Please log in first")
    registry = _current_session_registry()
    if registry is None:
        raise gr.Error(
            "Please log in again; application services are not initialized"
        )
    try:
        return registry.get_session(session_token)
    except InvalidSessionError as exc:
        raise gr.Error(str(exc))


def start_import_workers() -> None:
    """Start the shared import workers for the script entry point."""

    initialize_app_services()
    services.start()


def _empty_dropdowns():
    return gr.update(choices=[], value=[]), gr.update(choices=[], value=[])


def _get_current_dropdown_value(session_token):
    """根据 current_document_id 找到下拉框当前值"""

    assistant = _require_assistant(session_token)
    choices = assistant.get_documents()

    current_value = None

    if assistant.current_document_id:
        for choice in choices:
            if choice.endswith(assistant.current_document_id):
                current_value = choice
                break

    return choices, current_value


def register_user(username, password):
    try:
        registry = _current_session_registry()
        token = registry.register(username or "", password or "")
        session = registry.get_session(token)
        choices, current_value = _get_current_dropdown_value(token)
        update = gr.update(choices=choices, value=[current_value] if current_value else [])
        return token, f"Logged in as {session.username}", update, update
    except Exception as exc:
        empty_ask, empty_search = _empty_dropdowns()
        return "", f"Register failed: {exc}", empty_ask, empty_search


def login_user(username, password):
    try:
        registry = _current_session_registry()
        token = registry.login(username or "", password or "")
        session = registry.get_session(token)
        choices, current_value = _get_current_dropdown_value(token)
        update = gr.update(choices=choices, value=[current_value] if current_value else [])
        return token, f"Logged in as {session.username}", update, update
    except Exception as exc:
        empty_ask, empty_search = _empty_dropdowns()
        return "", f"Login failed: {exc}", empty_ask, empty_search


def logout_user(session_token):
    _require_session(session_token)
    _current_session_registry().logout(session_token)
    empty_ask, empty_search = _empty_dropdowns()
    return "", "Logged out", empty_ask, empty_search


def upload_document(session_token, file):
    """Backward-compatible single-file entry point for asynchronous imports."""

    _require_session(session_token)
    if file is None:
        raise gr.Error("Please select at least one document")
    return submit_import_batch(session_token, [file])


_IMPORT_STATUS_LABELS = {
    "queued": "排队中",
    "running": "执行中",
    "retry_wait": "等待重试",
    "succeeded": "成功",
    "failed": "失败",
}
_IMPORT_STAGE_LABELS = {
    "queued": "排队中",
    "staged": "已暂存",
    "parsing": "解析文档",
    "chunking": "切分文档",
    "embedding": "生成嵌入",
    "persisting": "持久化",
    "committing": "提交结果",
    "succeeded": "已完成",
    "failed": "失败",
}
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    ["']?(?:api[_-]?key|access[_-]?token|authorization|auth|token|key|
    password|passwd|pwd|secret|client[_-]?secret|credential)["']?
    \s*[=:]\s*
    (?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]\r\n]+)
    """
)
_BEARER_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:authorization\s*:\s*)?bearer\s+[^\s,;}\]\r\n]+"
)
_URL_USERINFO_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@"
)
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:[\\/][^\s'\"]+")
_UNIX_PATH_RE = re.compile(r"(?<!\w)/(?:[^/\s'\"]+/)+[^\s'\"]*")


def _format_import_timestamp(value):
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "-"


def _format_import_error(task) -> str:
    private_values = (
        task.user_id,
        task.staged_relative_path,
        task.document_id,
        task.task_id,
        task.batch_id,
    )
    safe = sanitize_error_message(
        task.error_summary or "",
        secrets=private_values,
    )
    for value in private_values:
        if value:
            safe = safe.replace(str(value), "[redacted]")
    safe = _URL_USERINFO_RE.sub(r"\1[credentials-redacted]@", safe)
    safe = _BEARER_CREDENTIAL_RE.sub("[credentials-redacted]", safe)
    safe = _CREDENTIAL_ASSIGNMENT_RE.sub("[credentials-redacted]", safe)
    safe = _WINDOWS_PATH_RE.sub("[路径已脱敏]", safe)
    safe = _UNIX_PATH_RE.sub("[路径已脱敏]", safe)
    return safe[:500]


def format_batch_summary(summary: ImportBatchSummary) -> str:
    return (
        "批量导入进度\n\n"
        f"总数：{summary.total}　排队：{summary.queued}　执行中：{summary.running}　"
        f"等待重试：{summary.retry_wait}　成功：{summary.succeeded}　失败：{summary.failed}"
    )


def format_task_table(summary: ImportBatchSummary) -> list[list[object]]:
    rows: list[list[object]] = []
    for task in summary.tasks:
        rows.append(
            [
                task.original_name,
                _IMPORT_STATUS_LABELS.get(task.status, "未知"),
                _IMPORT_STAGE_LABELS.get(task.stage, "未知"),
                int(task.progress),
                int(task.total_attempt_count),
                _format_import_timestamp(task.next_attempt_at),
                _format_import_error(task),
            ]
        )
    return rows


def _batch_choice(summary: ImportBatchSummary):
    label = (
        f"{_format_import_timestamp(summary.created_at)} - "
        f"{summary.succeeded}/{summary.total} succeeded"
    )
    return (label, summary.batch_id)


def submit_import_batch(session_token, files, progress=gr.Progress()):
    """Stage a batch for background import after authenticating the session."""

    _require_session(session_token)
    if not files:
        raise gr.Error("Please select at least one document")
    try:
        summary = _current_import_service().submit_batch(
            session_token,
            files,
            progress=progress,
        )
    except (ValueError, KeyError) as exc:
        raise gr.Error(str(exc))
    document_update = gr.update()
    try:
        choices, current_value = _get_current_dropdown_value(session_token)
        document_update = gr.update(
            choices=choices,
            value=[current_value] if current_value else [],
        )
    except gr.Error:
        raise
    return (
        summary.batch_id,
        format_batch_summary(summary),
        format_task_table(summary),
        document_update,
        "",
    )


def refresh_import_batches(session_token):
    if not session_token:
        return gr.update(choices=[], value=None), "", [], ""
    _require_session(session_token)
    batches = _current_import_service().list_batches(session_token, limit=50)
    if not batches:
        return gr.update(choices=[], value=None), "", [], ""
    selected = batches[0]
    return (
        gr.update(
            choices=[_batch_choice(batch) for batch in batches],
            value=selected.batch_id,
        ),
        format_batch_summary(selected),
        format_task_table(selected),
        "",
    )


def clear_import_ui():
    return gr.update(choices=[], value=None), "", [], ""


def refresh_import_batch(session_token, batch_id, selected_task=""):
    if not session_token:
        return "", [], ""
    _require_session(session_token)
    if not batch_id:
        return "", [], ""
    try:
        summary = _current_import_service().get_batch(session_token, batch_id)
    except KeyError as exc:
        raise gr.Error(str(exc))
    return (
        format_batch_summary(summary),
        format_task_table(summary),
        _retain_import_task_selection(summary, batch_id, selected_task),
    )


def select_import_task(session_token, batch_id, evt: gr.SelectData):
    """Resolve a selected failed row to its server-owned opaque task ID."""

    if not session_token:
        return ""
    _require_session(session_token)
    if not batch_id:
        return ""
    try:
        summary = _current_import_service().get_batch(session_token, batch_id)
    except KeyError as exc:
        raise gr.Error(str(exc))
    row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    if not isinstance(row_index, int) or not 0 <= row_index < len(summary.tasks):
        return ""
    task = summary.tasks[row_index]
    return (batch_id, task.task_id) if task.status == "failed" else ""


def retry_import_task(session_token, batch_id, selected_task=""):
    _require_session(session_token)
    selection = _import_task_selection(selected_task)
    if selection is None:
        raise gr.Error("Please select a failed task")
    selected_batch_id, task_id = selection
    if not batch_id or selected_batch_id != batch_id:
        raise gr.Error("Selected task does not belong to the visible batch")
    try:
        summary = _current_import_service().retry_task(
            session_token, task_id, expected_batch_id=batch_id
        )
    except (ValueError, KeyError) as exc:
        raise gr.Error(str(exc))
    return format_batch_summary(summary), format_task_table(summary), ""


def retry_import_batch_failures(session_token, batch_id):
    _require_session(session_token)
    if not batch_id:
        raise gr.Error("Please select an import batch")
    try:
        summary = _current_import_service().retry_failed_in_batch(
            session_token,
            batch_id,
        )
    except (ValueError, KeyError) as exc:
        raise gr.Error(str(exc))
    return format_batch_summary(summary), format_task_table(summary), ""


def _import_task_selection(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    batch_id, task_id = value
    if not isinstance(batch_id, str) or not isinstance(task_id, str):
        return None
    if not batch_id or not task_id:
        return None
    return batch_id, task_id


def _retain_import_task_selection(summary, batch_id, selected_task):
    selection = _import_task_selection(selected_task)
    if selection is None or selection[0] != batch_id:
        return ""
    selected_task_id = selection[1]
    if any(
        task.task_id == selected_task_id and task.status == "failed"
        for task in summary.tasks
    ):
        return selection
    return ""


def refresh_documents(session_token):
    """刷新 文档 下拉列表"""

    choices, current_value = _get_current_dropdown_value(session_token)

    return gr.update(
        choices=choices,
        value=[current_value] if current_value else []
    )


def select_document(session_token, selected):
    """切换当前 文档"""

    assistant = _require_assistant(session_token)

    try:
        selected = primary_document_label(selected)
    except ValueError:
        return "❌ 切换当前文档只能选择 1 篇文档"

    return assistant.select_document(selected)


def delete_current_pdf(session_token, selected=None):
    """删除当前 文档，并刷新两个下拉框"""

    # 如果用户在下拉框里选了 文档，先切换为当前 文档
    assistant = _require_assistant(session_token)

    if selected:
        try:
            selected = primary_document_label(selected)
        except ValueError:
            return (
                "❌ 删除当前文档只能选择 1 篇文档",
                gr.update(),
                gr.update(),
                "",
                "",
            )
        assistant.select_document(selected)

    result = assistant.delete_current_document()

    choices, current_value = _get_current_dropdown_value(session_token)

    dropdown_update = gr.update(
        choices=choices,
        value=[current_value] if current_value else []
    )

    empty_status = ""

    return (
        result,
        dropdown_update,
        dropdown_update,
        empty_status,
        empty_status,
    )

def clear_all_pdfs(session_token):
    """清空全部 文档，并刷新两个下拉框"""

    assistant = _require_assistant(session_token)
    result = assistant.clear_all_documents()

    # 清空后，下拉框应该为空
    choices, current_value = _get_current_dropdown_value(session_token)

    dropdown_update = gr.update(
        choices=choices,
        value=[current_value] if current_value else []
    )

    empty_status = ""

    return (
        result,
        dropdown_update,
        dropdown_update,
        empty_status,
        empty_status,
    )


def _legacy_ask_pdf_unused(question, selected_pdf=None):
    """文档 问答：如果下拉框选择了 文档，则先切换当前 文档"""

    if not question or not question.strip():
        return "❌ 请输入问题"

    if selected_pdf:
        assistant.select_document(selected_pdf)

    if not assistant.current_document_id:
        return "❌ 当前没有选择 文档。请先在下拉框中选择一个 文档，并点击“切换当前文档”。"

    try:
        return assistant.ask(question, limit=5)
    except Exception as e:
        return f"❌ 文档 问答失败: {e}"


def _legacy_search_pdf_unused(query, selected_pdf=None):
    """文档检索：如果下拉框选择了 文档，则先切换当前 文档，并返回调试信息"""

    if not query or not query.strip():
        return "❌ 请输入检索内容"

    if selected_pdf:
        select_result = assistant.select_document(selected_pdf)
    else:
        select_result = "⚠️ 当前没有从下拉框传入 文档"

    if not assistant.current_document_id:
        return (
            "❌ 当前没有选择 文档。\n"
            "请先在下拉框中选择一个 文档，并点击“切换当前文档”。\n\n"
            f"当前下拉框值: {selected_pdf}\n"
            f"当前 document_id: {assistant.current_document_id}"
        )

    try:
        result = assistant.search(query, limit=5)

        if result is None:
            result = ""

        result = str(result)

        if not result.strip():
            return (
                "⚠️ 检索函数执行了，但返回内容为空。\n\n"
                f"当前关键词: {query}\n"
                f"当前下拉框值: {selected_pdf}\n"
                f"当前 document_id: {assistant.current_document_id}\n"
                f"切换结果:\n{select_result}\n\n"
                "建议检查 pdf_learning_assistant.py 中的 search() 方法是否有 return。"
            )

        return (
            f"📌 当前检索 文档:\n"
            f"- selected_pdf: {selected_pdf}\n"
            f"- document_id: {assistant.current_document_id}\n\n"
            f"{result}"
        )

    except Exception as e:
        return (
            f"❌ 文档检索失败: {e}\n\n"
            f"当前关键词: {query}\n"
            f"当前下拉框值: {selected_pdf}\n"
            f"当前 document_id: {assistant.current_document_id}"
        )

def ask_pdf(session_token, question, selected_pdf=None, qa_mode="auto"):
    """Document QA for one or more selected documents."""

    assistant = _require_assistant(session_token)

    if not question or not question.strip():
        return "❌ 请输入问题"

    try:
        return assistant.ask(
            question,
            limit=5,
            selected_documents=selected_pdf,
            mode=qa_mode or "auto",
            structured_output=(qa_mode == "compare"),
        )
    except Exception as e:
        return f"❌ 文档问答失败: {e}"


def _format_answer_sources(sources):
    if not sources:
        return "暂无可定位来源"
    grouped = {}
    for source in sources:
        document_id = source.get("document_id") or "unknown"
        grouped.setdefault(document_id, []).append(source)
    lines = []
    for document_id, items in grouped.items():
        lines.append(f"## {document_id}")
        for item in items:
            lines.append(item.get("reference") or f"[{item.get('citation_id', '')}]")
            if item.get("file_name"):
                lines.append(f"文件: {item['file_name']}")
            if item.get("excerpt"):
                lines.append(f"原文片段: {item['excerpt']}")
            lines.append("")
    return "\n".join(lines).strip()


def ask_pdf_with_sources(session_token, question, selected_pdf=None, qa_mode="auto"):
    answer = ask_pdf(session_token, question, selected_pdf, qa_mode)
    try:
        assistant = _require_assistant(session_token)
        data = dict(getattr(assistant.rag_tool, "_last_action_data", {}) or {})
        return answer, _format_answer_sources(data.get("sources") or [])
    except Exception:
        return answer, "暂无可定位来源"


def _format_summary_task(task):
    status = task.get("status", "unknown")
    completed = task.get("completed", 0)
    total = task.get("total", 0)
    stage = task.get("stage", "")
    lines = [
        f"任务状态: {status}",
        f"进度: {completed}/{total}",
        f"阶段: {stage}",
    ]
    if task.get("current_document_id"):
        lines.append(f"当前文档: {task['current_document_id']}")
    if task.get("error"):
        lines.append(f"错误: {task['error']}")
    if task.get("result"):
        lines.extend(["", task["result"]])
    return "\n".join(lines)


def start_summary_pdf(session_token, question, selected_pdf=None):
    assistant = _require_assistant(session_token)
    try:
        task = assistant.start_summary_task(
            question,
            selected_documents=selected_pdf or [],
            limit=5,
        )
        return task["task_id"], _format_summary_task(task)
    except Exception as error:
        return "", f"后台总结启动失败: {error}"


def poll_summary_pdf(session_token, task_id):
    assistant = _require_assistant(session_token)
    if not task_id:
        return "当前没有后台总结任务"
    try:
        return _format_summary_task(assistant.get_summary_task(task_id))
    except Exception as error:
        return f"查询总结任务失败: {error}"


def cancel_summary_pdf(session_token, task_id):
    assistant = _require_assistant(session_token)
    if not task_id:
        return "当前没有可取消的后台总结任务"
    try:
        return _format_summary_task(assistant.cancel_summary_task(task_id))
    except Exception as error:
        return f"取消总结任务失败: {error}"


def start_summary_pdf_auto(session_token, question, selected_pdf=None):
    task_id, output = start_summary_pdf(session_token, question, selected_pdf)
    return task_id, output, gr.update(active=bool(task_id))


def poll_summary_pdf_auto(session_token, task_id):
    assistant = _require_assistant(session_token)
    if not task_id:
        return "当前没有后台总结任务", gr.update(active=False)
    try:
        task = assistant.get_summary_task(task_id)
        active = task.get("status") not in {"completed", "failed", "cancelled"}
        return _format_summary_task(task), gr.update(active=active)
    except Exception as error:
        return f"查询总结任务失败: {error}", gr.update(active=False)


def cancel_summary_pdf_auto(session_token, task_id):
    output = cancel_summary_pdf(session_token, task_id)
    return output, gr.update(active=False)


def search_pdf(session_token, query, selected_pdf=None):
    """Document search for one or more selected documents."""

    assistant = _require_assistant(session_token)

    if not query or not query.strip():
        return "❌ 请输入检索内容"

    try:
        result = assistant.search(
            query,
            limit=5,
            selected_documents=selected_pdf,
        )
        return (
            f"📌 当前检索文档:\n"
            f"- selected_pdf: {selected_pdf}\n\n"
            f"{result}"
        )
    except Exception as e:
        return f"❌ 文档检索失败: {e}"


def generate_citations(session_token, query, selected_pdf=None):
    """根据关键词生成可复制引用格式"""

    assistant = _require_assistant(session_token)

    if not query or not query.strip():
        return "❌ 请输入检索关键词"

    if selected_pdf:
        try:
            selected_pdf = primary_document_label(selected_pdf)
        except ValueError:
            return "❌ 生成引用只能选择 1 篇文档"
        assistant.select_document(selected_pdf)

    if not assistant.current_document_id:
        return "❌ 当前没有选择 文档。请先在下拉框中选择一个 文档。"

    try:
        result = assistant.rag_tool.execute(
            "citation",
            query=query,
            limit=5,
            min_score=0.0,
            document_id=assistant.current_document_id
        )

        return (
            f"📌 当前引用 文档:\n"
            f"- selected_pdf: {selected_pdf}\n"
            f"- document_id: {assistant.current_document_id}\n\n"
            f"{result}"
        )

    except Exception as e:
        return (
            f"❌ 生成引用格式失败: {e}\n\n"
            f"当前关键词: {query}\n"
            f"当前下拉框值: {selected_pdf}\n"
            f"当前 document_id: {assistant.current_document_id}"
        )


def add_note(session_token, note, concept):
    assistant = _require_assistant(session_token)

    if not note or not note.strip():
        return "❌ 请输入学习笔记"

    return assistant.add_note(note=note, concept=concept)


def clear_all_notes(session_token):
    """清空全部学习笔记"""

    assistant = _require_assistant(session_token)

    try:
        return assistant.clear_all_notes()
    except Exception as e:
        return f"❌ 清空学习笔记失败: {e}"


def recall_memory(session_token, query):
    assistant = _require_assistant(session_token)

    if not query or not query.strip():
        return "❌ 请输入要回忆的内容"

    return assistant.recall(query, limit=5)


def show_stats(session_token):
    assistant = _require_assistant(session_token)
    return assistant.get_stats()


def generate_report(session_token):
    assistant = _require_assistant(session_token)
    return assistant.generate_report()

def _report_id(selected):
    if not selected:
        raise gr.Error("Please select a report")
    return str(selected).split("|")[-1].strip()


def list_reports(session_token):
    assistant = _require_assistant(session_token)
    return gr.update(choices=[
        f"{record.created_at} - {record.title} | {record.id}"
        for record in assistant.report_service.list_reports(assistant.user_id)
    ])


def view_report(session_token, selected):
    assistant = _require_assistant(session_token)
    return assistant.report_service.read_report(assistant.user_id, _report_id(selected))


def download_report_markdown(session_token, selected):
    assistant = _require_assistant(session_token)
    return str(assistant.report_service.report_file_path(
        assistant.user_id, _report_id(selected)
    ))


def export_report_docx(session_token, selected=None):
    assistant = _require_assistant(session_token)
    path = assistant.export_report_docx(_report_id(selected) if selected else None)
    return path

def export_report_markdown(session_token):
    assistant = _require_assistant(session_token)
    path = assistant.export_report_markdown()
    return path


def scan_legacy_data(session_token):
    _require_assistant(session_token)
    return json.dumps(
        _current_legacy_migration().scan(),
        ensure_ascii=False,
        indent=2,
    )


def claim_legacy_data(session_token):
    assistant = _require_assistant(session_token)
    return str(_current_legacy_migration().claim(assistant.user_id))


def _get_recovery(session_token):
    """Return the RecoveryService for the authenticated session."""
    assistant = _require_assistant(session_token)
    if assistant.runtime is None:
        raise gr.Error("Recovery not available: no runtime configured")
    return assistant.runtime.recovery


def check_corruption_status(session_token):
    """Inspect History and Memory for corruption."""
    recovery = _get_recovery(session_token)
    history = recovery.check_history()
    memory = recovery.check_memory()
    lines = []
    for r in (history, memory):
        icon = "✅" if r.success else "❌"
        lines.append(f"{icon} {r.target}: {r.message}")
    return "\n\n".join(lines)


def quarantine_history(session_token):
    """Quarantine corrupt History and create a clean active file."""
    recovery = _get_recovery(session_token)
    result = recovery.quarantine_history()
    return f"{'✅' if result.success else '❌'} {result.message}"


def quarantine_memory(session_token):
    """Quarantine corrupt Memory snapshot and create a clean active file."""
    recovery = _get_recovery(session_token)
    result = recovery.quarantine_memory()
    return f"{'✅' if result.success else '❌'} {result.message}"


def list_recovery_backups(session_token):
    """Refresh the backup dropdown with available opaque backup IDs."""
    recovery = _get_recovery(session_token)
    history_backups = recovery.list_history_backups()
    memory_backups = recovery.list_memory_backups()
    all_backups = history_backups + memory_backups
    return gr.update(choices=all_backups, value=[] if not all_backups else None)


def restore_history(session_token, backup_id):
    """Validate and restore a History backup by opaque ID."""
    if not backup_id:
        return "❌ Please select a backup first"
    recovery = _get_recovery(session_token)
    try:
        result = recovery.restore_history(backup_id)
    except (ValueError, FileNotFoundError) as exc:
        return f"❌ Invalid backup: {exc}"
    return f"{'✅' if result.success else '❌'} {result.message}"


def restore_memory(session_token, backup_id):
    """Validate and restore a Memory backup by opaque ID."""
    if not backup_id:
        return "❌ Please select a backup first"
    recovery = _get_recovery(session_token)
    try:
        result = recovery.restore_memory(backup_id)
    except (ValueError, FileNotFoundError) as exc:
        return f"❌ Invalid backup: {exc}"
    return f"{'✅' if result.success else '❌'} {result.message}"


def _build_demo(
    service_provider: Callable[[], ApplicationServices] | None = None,
) -> gr.Blocks:
    with gr.Blocks(title="文档 智能学习助手") as demo:
        session_token = gr.State("")
        with gr.Tab("登录 / 注册"):
            username_input = gr.Textbox(label="用户名")
            password_input = gr.Textbox(label="密码", type="password")
            with gr.Row():
                login_btn = gr.Button("登录")
                register_btn = gr.Button("注册")
                logout_btn = gr.Button("退出登录")
            auth_status = gr.Textbox(label="登录状态", interactive=False)
        gr.Markdown("# 📘 智能文档学习助手")
        gr.Markdown("支持 文档 / TXT / Markdown / Word 导入、文档问答、学习笔记、记忆回忆和学习统计。")

        # =========================
        # 1. 上传文档
        # =========================
        with gr.Tab("1. 上传文档"):
            import_files = gr.File(
                label="批量上传文档",
                file_count="multiple",
                type="filepath",
                file_types=[".pdf", ".txt", ".md", ".docx"]
            )

            submit_import_btn = gr.Button("提交导入")

            import_batch_dropdown = gr.Dropdown(
                label="最近批次",
                choices=[],
                interactive=True,
            )
            import_summary = gr.Markdown()
            import_tasks = gr.Dataframe(
                headers=["文件名", "状态", "阶段", "进度", "尝试次数", "下次重试", "错误"],
                datatype=["str", "str", "str", "number", "number", "str", "str"],
                interactive=False,
            )
            submitted_import_batch_id = gr.State("")
            selected_import_task_id = gr.State("")
            with gr.Row():
                retry_selected_btn = gr.Button("重试所选失败项")
                retry_batch_btn = gr.Button("重试本批次全部失败项")
                refresh_import_btn = gr.Button("手动刷新")
            import_timer = gr.Timer(value=1, active=True)

        # =========================
        # 2. 文档 问答
        # =========================
        with gr.Tab("2. 文档问答"):
            doc_dropdown_ask = gr.Dropdown(
                label="请选择当前文档",
                choices=[],
                multiselect=True,
                max_choices=10,
                interactive=True
            )

            qa_mode_radio = gr.Radio(
                label="问答模式",
                choices=[
                    ("自动", "auto"),
                    ("联合问答", "joint"),
                    ("对比分析", "compare"),
                    ("联合总结", "summary"),
                ],
                value="auto",
                interactive=True,
            )

            select_doc_btn_ask = gr.Button("切换当前文档")

            select_doc_output_ask = gr.Textbox(
                label="当前文档状态",
                lines=4
            )

            refresh_doc_btn_ask = gr.Button("刷新文档列表")

            delete_doc_btn_ask = gr.Button("删除当前文档")

            clear_all_doc_btn_ask = gr.Button("清空全部文档")

            delete_doc_output_ask = gr.Textbox(
                label="删除 / 清空结果",
                lines=8
            )

            question_input = gr.Textbox(
                label="请输入问题",
                lines=3,
                placeholder="例如：这个 文档 主要讲了什么？"
            )

            ask_btn = gr.Button("开始问答")

            answer_output = gr.Textbox(
                label="回答结果",
                lines=15
            )

            answer_sources_output = gr.Textbox(
                label="来源定位与可复制引用",
                lines=10,
            )
            summary_task_id = gr.State("")
            summary_poll_timer = gr.Timer(1.0, active=False)
            with gr.Row():
                start_summary_btn = gr.Button("后台联合总结")
                poll_summary_btn = gr.Button("刷新总结进度")
                cancel_summary_btn = gr.Button("取消后台总结")
            summary_task_output = gr.Textbox(
                label="后台总结任务",
                lines=8,
            )

            select_doc_btn_ask.click(
                fn=select_document,
                inputs=[session_token, doc_dropdown_ask],
                outputs=select_doc_output_ask
            )

            refresh_doc_btn_ask.click(
                fn=refresh_documents,
                inputs=session_token,
                outputs=doc_dropdown_ask
            )

            ask_btn.click(
                fn=ask_pdf_with_sources,
                inputs=[session_token, question_input, doc_dropdown_ask, qa_mode_radio],
                outputs=[answer_output, answer_sources_output]
            )

            start_summary_btn.click(
                fn=start_summary_pdf_auto,
                inputs=[session_token, question_input, doc_dropdown_ask],
                outputs=[summary_task_id, summary_task_output, summary_poll_timer],
            )

            poll_summary_btn.click(
                fn=poll_summary_pdf,
                inputs=[session_token, summary_task_id],
                outputs=summary_task_output,
            )

            cancel_summary_btn.click(
                fn=cancel_summary_pdf_auto,
                inputs=[session_token, summary_task_id],
                outputs=[summary_task_output, summary_poll_timer],
            )

            summary_poll_timer.tick(
                fn=poll_summary_pdf_auto,
                inputs=[session_token, summary_task_id],
                outputs=[summary_task_output, summary_poll_timer],
                show_progress="hidden",
            )

        # =========================
        # 3. 文档检索
        # =========================
        with gr.Tab("3.文献检索"):
            doc_dropdown_search = gr.Dropdown(
                label="请选择当前文档",
                choices=[],
                multiselect=True,
                max_choices=10,
                interactive=True
            )

            select_doc_btn_search = gr.Button("切换当前文档")

            select_doc_output_search = gr.Textbox(
                label="当前 文档 状态",
                lines=4
            )

            refresh_doc_btn_search = gr.Button("刷新文档列表")

            delete_doc_btn_search = gr.Button("删除当前文档")

            clear_all_doc_btn_search = gr.Button("清空全部文档")

            delete_doc_output_search = gr.Textbox(
                label="删除 / 清空结果",
                lines=8
            )

            search_input = gr.Textbox(
                label="搜索关键词",
                lines=2,
                placeholder="例如：自由、LLM、模型下载、SFT"
            )

            search_btn = gr.Button("搜索")

            search_output = gr.Textbox(
                label="检索结果",
                lines=15
            )

            citation_btn = gr.Button("生成引用格式")

            citation_output = gr.Textbox(
                label="可复制引用格式",
                lines=15
            )

            select_doc_btn_search.click(
                fn=select_document,
                inputs=[session_token, doc_dropdown_search],
                outputs=select_doc_output_search
            )

            refresh_doc_btn_search.click(
                fn=refresh_documents,
                inputs=session_token,
                outputs=doc_dropdown_search
            )

            search_btn.click(
                fn=search_pdf,
                inputs=[session_token, search_input, doc_dropdown_search],
                outputs=search_output
            )

            citation_btn.click(
                fn=generate_citations,
                inputs=[session_token, search_input, doc_dropdown_search],
                outputs=citation_output
            )

        # =========================
        # 4. 学习笔记
        # =========================
        with gr.Tab("4.学习笔记"):
            concept_input = gr.Textbox(
                label="概念名称，可选",
                placeholder="例如：RAG、LLM、亲子沟通"
            )

            note_input = gr.Textbox(
                label="学习笔记",
                lines=5,
                placeholder="写下你对这个概念的理解"
            )

            note_btn = gr.Button("保存笔记")

            clear_notes_btn = gr.Button("清空全部学习笔记")

            note_output = gr.Textbox(
                label="保存 / 清空结果",
                lines=8
            )

            note_btn.click(
                fn=add_note,
                inputs=[session_token, note_input, concept_input],
                outputs=note_output
            )

            clear_notes_btn.click(
                fn=clear_all_notes,
                inputs=session_token,
                outputs=note_output
            )

        # =========================
        # 5. 记忆回忆
        # =========================
        with gr.Tab("5.记忆回忆"):
            recall_input = gr.Textbox(
                label="你想回忆什么？",
                lines=2,
                placeholder="例如：RAG 核心、亲子沟通技巧"
            )

            recall_btn = gr.Button("回忆")

            recall_output = gr.Textbox(
                label="回忆结果",
                lines=15
            )

            recall_btn.click(
                fn=recall_memory,
                inputs=[session_token, recall_input],
                outputs=recall_output
            )

        # =========================
        # 6. 学习统计
        # =========================
        with gr.Tab("6.学习统计"):
            stats_btn = gr.Button("查看统计")

            stats_output = gr.Textbox(
                label="统计结果",
                lines=18
            )

            stats_btn.click(
                fn=show_stats,
                inputs=session_token,
                outputs=stats_output
            )

        # =========================
        # 7. 学习报告
        # =========================
        with gr.Tab("7.学习报告"):
            report_btn = gr.Button("学习生成报告")

            report_output = gr.Textbox(
                label="学习报告",
                lines=25
            )

            export_report_md_btn = gr.Button("导出学习报告 Markdown")

            report_md_file = gr.File(
                label="下载 Markdown 学习报告"
            )

            export_report_docx_btn = gr.Button("导出学习报告 Word")

            report_docx_file = gr.File(
                label="下载 Word 学习报告"
            )

            report_history = gr.Dropdown(label="Report history", choices=[])
            refresh_reports_btn = gr.Button("Refresh reports")
            view_report_btn = gr.Button("View snapshot")
            download_snapshot_btn = gr.Button("Download Markdown snapshot")

            report_btn.click(
                fn=generate_report,
                inputs=session_token,
                outputs=report_output
            )

            export_report_md_btn.click(
                fn=export_report_markdown,
                inputs=session_token,
                outputs=report_md_file
            )

            export_report_docx_btn.click(
                fn=export_report_docx,
                inputs=[session_token, report_history],
                outputs=report_docx_file
            )
            refresh_reports_btn.click(
                fn=list_reports, inputs=session_token, outputs=report_history
            )
            view_report_btn.click(
                fn=view_report, inputs=[session_token, report_history], outputs=report_output
            )
            download_snapshot_btn.click(
                fn=download_report_markdown,
                inputs=[session_token, report_history],
                outputs=report_md_file,
            )

            with gr.Accordion("Legacy data migration", open=False):
                migration_output = gr.Textbox(lines=10)
                scan_migration_btn = gr.Button("Scan legacy data")
                claim_migration_btn = gr.Button("Claim and migrate")
                scan_migration_btn.click(
                    fn=scan_legacy_data, inputs=session_token, outputs=migration_output
                )
                claim_migration_btn.click(
                    fn=claim_legacy_data, inputs=session_token, outputs=migration_output
                )

            with gr.Accordion("🛠️ Data Recovery", open=False):
                recovery_check_btn = gr.Button("Check corruption status")
                recovery_status = gr.Textbox(
                    label="Corruption status",
                    lines=4,
                    interactive=False,
                )
                gr.Markdown("---\n### History recovery")
                with gr.Row():
                    quarantine_history_btn = gr.Button("Quarantine corrupt History")
                recovery_history_output = gr.Textbox(
                    label="History recovery result",
                    lines=3,
                    interactive=False,
                )
                gr.Markdown("### Memory recovery")
                with gr.Row():
                    quarantine_memory_btn = gr.Button("Quarantine corrupt Memory")
                recovery_memory_output = gr.Textbox(
                    label="Memory recovery result",
                    lines=3,
                    interactive=False,
                )
                gr.Markdown("### Restore from backup")
                recovery_backup_dropdown = gr.Dropdown(
                    label="Select backup to restore",
                    choices=[],
                    interactive=True,
                )
                with gr.Row():
                    refresh_backups_btn = gr.Button("Refresh backup list")
                    restore_history_btn = gr.Button("Restore History")
                    restore_memory_btn = gr.Button("Restore Memory")
                recovery_restore_output = gr.Textbox(
                    label="Restore result",
                    lines=3,
                    interactive=False,
                )

                recovery_check_btn.click(
                    fn=check_corruption_status,
                    inputs=session_token,
                    outputs=recovery_status,
                )
                quarantine_history_btn.click(
                    fn=quarantine_history,
                    inputs=session_token,
                    outputs=recovery_history_output,
                )
                quarantine_memory_btn.click(
                    fn=quarantine_memory,
                    inputs=session_token,
                    outputs=recovery_memory_output,
                )
                refresh_backups_btn.click(
                    fn=list_recovery_backups,
                    inputs=session_token,
                    outputs=recovery_backup_dropdown,
                )
                restore_history_btn.click(
                    fn=restore_history,
                    inputs=[session_token, recovery_backup_dropdown],
                    outputs=recovery_restore_output,
                )
                restore_memory_btn.click(
                    fn=restore_memory,
                    inputs=[session_token, recovery_backup_dropdown],
                    outputs=recovery_restore_output,
                )

            delete_doc_btn_ask.click(
                fn=delete_current_pdf,
                inputs=[session_token, doc_dropdown_ask],
                outputs=[
                    delete_doc_output_ask,
                    doc_dropdown_ask,
                    doc_dropdown_search,
                    select_doc_output_ask,
                    select_doc_output_search,
                ],
            )

            delete_doc_btn_search.click(
                fn=delete_current_pdf,
                inputs=[session_token, doc_dropdown_search],
                outputs=[
                    delete_doc_output_search,
                    doc_dropdown_ask,
                    doc_dropdown_search,
                    select_doc_output_ask,
                    select_doc_output_search,
                ],
            )

            clear_all_doc_btn_ask.click(
                fn=clear_all_pdfs,
                inputs=session_token,
                outputs=[
                    delete_doc_output_ask,
                    doc_dropdown_ask,
                    doc_dropdown_search,
                    select_doc_output_ask,
                    select_doc_output_search,
                ],
            )

            clear_all_doc_btn_search.click(
                fn=clear_all_pdfs,
                inputs=session_token,
                outputs=[
                    delete_doc_output_search,
                    doc_dropdown_ask,
                    doc_dropdown_search,
                    select_doc_output_ask,
                    select_doc_output_search,
                ],
            )

            # =========================
            # 上传文档：所有组件都定义完之后再绑定
            # =========================
            login_btn.click(
                fn=login_user,
                inputs=[username_input, password_input],
                outputs=[session_token, auth_status, doc_dropdown_ask, doc_dropdown_search],
            ).then(
                fn=refresh_import_batches,
                inputs=session_token,
                outputs=[
                    import_batch_dropdown,
                    import_summary,
                    import_tasks,
                    selected_import_task_id,
                ],
                queue=False,
            )

            register_btn.click(
                fn=register_user,
                inputs=[username_input, password_input],
                outputs=[session_token, auth_status, doc_dropdown_ask, doc_dropdown_search],
            ).then(
                fn=refresh_import_batches,
                inputs=session_token,
                outputs=[
                    import_batch_dropdown,
                    import_summary,
                    import_tasks,
                    selected_import_task_id,
                ],
                queue=False,
            )

            logout_btn.click(
                fn=logout_user,
                inputs=session_token,
                outputs=[session_token, auth_status, doc_dropdown_ask, doc_dropdown_search],
            ).then(
                fn=clear_import_ui,
                inputs=None,
                outputs=[
                    import_batch_dropdown,
                    import_summary,
                    import_tasks,
                    selected_import_task_id,
                ],
                queue=False,
            )

            submit_import_btn.click(
                fn=submit_import_batch,
                inputs=[session_token, import_files],
                outputs=[
                    submitted_import_batch_id,
                    import_summary,
                    import_tasks,
                    doc_dropdown_ask,
                    selected_import_task_id,
                ]
            ).then(
                fn=refresh_import_batches,
                inputs=session_token,
                outputs=[
                    import_batch_dropdown,
                    import_summary,
                    import_tasks,
                    selected_import_task_id,
                ],
                queue=False,
            )

            refresh_import_btn.click(
                fn=refresh_import_batches,
                inputs=session_token,
                outputs=[
                    import_batch_dropdown,
                    import_summary,
                    import_tasks,
                    selected_import_task_id,
                ],
                queue=False,
            )
            import_batch_dropdown.change(
                fn=refresh_import_batch,
                inputs=[
                    session_token,
                    import_batch_dropdown,
                    selected_import_task_id,
                ],
                outputs=[import_summary, import_tasks, selected_import_task_id],
                queue=False,
            )
            import_timer.tick(
                fn=refresh_import_batch,
                inputs=[
                    session_token,
                    import_batch_dropdown,
                    selected_import_task_id,
                ],
                outputs=[import_summary, import_tasks, selected_import_task_id],
                queue=False,
                show_progress="hidden",
            )
            import_tasks.select(
                fn=select_import_task,
                inputs=[session_token, import_batch_dropdown],
                outputs=selected_import_task_id,
                queue=False,
            )
            retry_selected_btn.click(
                fn=retry_import_task,
                inputs=[
                    session_token,
                    import_batch_dropdown,
                    selected_import_task_id,
                ],
                outputs=[import_summary, import_tasks, selected_import_task_id],
            )
            retry_batch_btn.click(
                fn=retry_import_batch_failures,
                inputs=[session_token, import_batch_dropdown],
                outputs=[import_summary, import_tasks, selected_import_task_id],
            )


    if service_provider is not None:
        for block_function in demo.fns.values():
            block_function.fn = _bind_handler(
                block_function.fn,
                service_provider,
            )
    return demo


def create_gradio_app(
    services: ApplicationServices | None = None,
    *,
    _service_provider: Callable[[], ApplicationServices] | None = None,
) -> gr.Blocks:
    """Return a new component tree bound to one application's services."""

    service_provider = _service_provider
    if services is not None:
        service_provider = lambda services=services: services
    return _build_demo(service_provider)


demo = create_gradio_app()


def launch_app() -> None:
    """Run the supported script lifecycle and always stop import workers."""

    application_services = initialize_app_services()
    try:
        start_import_workers()
        demo.launch(**load_launch_config().as_gradio_kwargs())
    finally:
        application_services.stop()


if __name__ == "__main__":
    launch_app()
