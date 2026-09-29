from datetime import datetime
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn

from app.report_docx import render_report_docx


def test_render_saved_report_content_and_owner():
    source = "📘 PDF 智能学习报告\n一、概况\n- 文档甲\n1. 第一项\n普通正文\n"
    data = render_report_docx(source, "owner-123", datetime(2026, 9, 29, 12, 30, 5))
    doc = Document(BytesIO(data))
    paragraphs = [p for p in doc.paragraphs if p.text]
    assert paragraphs[0].text == "PDF 智能学习报告"
    assert paragraphs[1].text == "生成时间：2026-09-29 12:30:05    用户：owner-123"
    assert [(p.text, p.style.name) for p in paragraphs[2:6]] == [
        ("一、概况", "Heading 2"), ("文档甲", "List Bullet"),
        ("1. 第一项", "List Number"), ("普通正文", "Normal"),
    ]
    assert paragraphs[-1].text == "—— 由 PDF 智能学习助手自动生成 ——"
    assert doc.styles["Normal"]._element.rPr.rFonts.get(qn("w:eastAsia")) == "微软雅黑"
    assert paragraphs[0].runs[0].font.size.pt == 20
