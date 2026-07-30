import sys
import shutil
from pathlib import Path

import gradio as gr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ui.launch_config import load_launch_config
from assistants.pdf_learning_assistant import PDFLearningAssistant


assistant = PDFLearningAssistant(user_id="user123")

UPLOAD_DIR = PROJECT_ROOT / "ui" / "knowledge_base" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _get_current_dropdown_value():
    """根据 current_document_id 找到下拉框当前值"""

    choices = assistant.get_documents()

    current_value = None

    if assistant.current_document_id:
        for choice in choices:
            if choice.endswith(assistant.current_document_id):
                current_value = choice
                break

    return choices, current_value


def upload_document(file):
    """上传文档，并自动刷新下拉框"""

    if file is None:
        return "❌ 请先上传文档文件", gr.update(), gr.update()

    source_path = Path(file.name)
    target_path = UPLOAD_DIR / source_path.name

    shutil.copy(str(source_path), str(target_path))

    result = assistant.load_document(str(target_path))

    choices, current_value = _get_current_dropdown_value()

    return (
        result,
        gr.update(choices=choices, value=current_value),
        gr.update(choices=choices, value=current_value),
    )


def refresh_documents():
    """刷新 文档 下拉列表"""

    choices, current_value = _get_current_dropdown_value()

    return gr.update(
        choices=choices,
        value=current_value
    )


def select_document(selected):
    """切换当前 文档"""

    return assistant.select_document(selected)


def delete_current_pdf(selected=None):
    """删除当前 文档，并刷新两个下拉框"""

    # 如果用户在下拉框里选了 文档，先切换为当前 文档
    if selected:
        assistant.select_document(selected)

    result = assistant.delete_current_document()

    choices, current_value = _get_current_dropdown_value()

    dropdown_update = gr.update(
        choices=choices,
        value=current_value
    )

    empty_status = ""

    return (
        result,
        dropdown_update,
        dropdown_update,
        empty_status,
        empty_status,
    )

def clear_all_pdfs():
    """清空全部 文档，并刷新两个下拉框"""

    result = assistant.clear_all_documents()

    # 清空后，下拉框应该为空
    choices, current_value = _get_current_dropdown_value()

    dropdown_update = gr.update(
        choices=choices,
        value=current_value
    )

    empty_status = ""

    return (
        result,
        dropdown_update,
        dropdown_update,
        empty_status,
        empty_status,
    )


def ask_pdf(question, selected_pdf=None):
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


def search_pdf(query, selected_pdf=None):
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

def generate_citations(query, selected_pdf=None):
    """根据关键词生成可复制引用格式"""

    if not query or not query.strip():
        return "❌ 请输入检索关键词"

    if selected_pdf:
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


def add_note(note, concept):
    if not note or not note.strip():
        return "❌ 请输入学习笔记"

    return assistant.add_note(note=note, concept=concept)


def clear_all_notes():
    """清空全部学习笔记"""

    try:
        return assistant.clear_all_notes()
    except Exception as e:
        return f"❌ 清空学习笔记失败: {e}"


def recall_memory(query):
    if not query or not query.strip():
        return "❌ 请输入要回忆的内容"

    return assistant.recall(query, limit=5)


def show_stats():
    return assistant.get_stats()


def generate_report():
    return assistant.generate_report()

def export_report_docx():
    path = assistant.export_report_docx()
    return path

def export_report_markdown():
    path = assistant.export_report_markdown()
    return path



with gr.Blocks(title="文档 智能学习助手") as demo:
    gr.Markdown("# 📘 智能文档学习助手")
    gr.Markdown("支持 文档 / TXT / Markdown / Word 导入、文档问答、学习笔记、记忆回忆和学习统计。")

    # =========================
    # 1. 上传文档
    # =========================
    with gr.Tab("1. 上传文档"):
        pdf_file = gr.File(
            label="上传文档文件",
            file_types=[".pdf", ".txt", ".md", ".docx"]
        )

        upload_btn = gr.Button("导入文档")

        upload_output = gr.Textbox(
            label="文档导入结果",
            lines=6
        )

    # =========================
    # 2. 文档 问答
    # =========================
    with gr.Tab("2. 文档问答"):
        doc_dropdown_ask = gr.Dropdown(
            label="请选择当前文档",
            choices=assistant.get_documents(),
            interactive=True
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

        select_doc_btn_ask.click(
            fn=select_document,
            inputs=doc_dropdown_ask,
            outputs=select_doc_output_ask
        )

        refresh_doc_btn_ask.click(
            fn=refresh_documents,
            inputs=None,
            outputs=doc_dropdown_ask
        )

        ask_btn.click(
            fn=ask_pdf,
            inputs=[question_input, doc_dropdown_ask],
            outputs=answer_output
        )

    # =========================
    # 3. 文档检索
    # =========================
    with gr.Tab("3.文献检索"):
        doc_dropdown_search = gr.Dropdown(
            label="请选择当前文档",
            choices=assistant.get_documents(),
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
            inputs=doc_dropdown_search,
            outputs=select_doc_output_search
        )

        refresh_doc_btn_search.click(
            fn=refresh_documents,
            inputs=None,
            outputs=doc_dropdown_search
        )

        search_btn.click(
            fn=search_pdf,
            inputs=[search_input, doc_dropdown_search],
            outputs=search_output
        )

        citation_btn.click(
            fn=generate_citations,
            inputs=[search_input, doc_dropdown_search],
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
            inputs=[note_input, concept_input],
            outputs=note_output
        )

        clear_notes_btn.click(
            fn=clear_all_notes,
            inputs=None,
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
            inputs=recall_input,
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
            inputs=None,
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

        report_btn.click(
            fn=generate_report,
            inputs=None,
            outputs=report_output
        )

        export_report_md_btn.click(
            fn=export_report_markdown,
            inputs=None,
            outputs=report_md_file
        )

        export_report_docx_btn.click(
            fn=export_report_docx,
            inputs=None,
            outputs=report_docx_file
        )

        # =========================
        # 上传文档：所有组件都定义完之后再绑定
        # =========================
        upload_btn.click(
            fn=upload_document,
            inputs=pdf_file,
            outputs=[
                upload_output,
                doc_dropdown_ask,
                doc_dropdown_search,
            ]
        )


if __name__ == "__main__":
    demo.launch(**load_launch_config().as_gradio_kwargs())
