# Resume Capability Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a new two-page Chinese PDF resume that converts the professional-skills keyword table into evidence-backed capability descriptions and applies the approved AI-era resume guidance without overwriting any existing PDF.

**Architecture:** Rebuild the resume with a temporary ReportLab canvas builder so text can reflow cleanly across two A4 pages. Extract the already-corrected portrait from the current optimized PDF, keep it at its native aspect ratio, and add a compact four-group skills section. Validate the generated artifact independently with pdfplumber, pypdf, PyMuPDF, Poppler rendering, and visual inspection.

**Tech Stack:** Python 3, ReportLab 4.4.9, pdfplumber 0.11.9, pypdf 6.10.0, PyMuPDF 1.26.3, Pillow, Poppler `pdftoppm`, Microsoft YaHei fonts.

## Global Constraints

- Source baseline: `D:/python_self_agent/output/pdf/冯嘉文_简历_大模型应用开发工程师_面试优化版.pdf`.
- Final output: `D:/python_self_agent/output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`.
- Do not overwrite the original desktop resume, the current engineering version, or the current interview-optimized version.
- Keep exactly two A4 pages, searchable Chinese text, one clickable GitHub link, and a portrait with no right-side frame gap.
- Keep the blue visual system and current overall identity; adopt the reference template's information hierarchy without copying it wholesale.
- The first page must show the main project title and at least three main-project bullets.
- Use only verified facts: 4 formats, 10-document joint QA, 20 files per batch, 4 concurrent users, 837 collected tests, and 43 currently passing GraphRAG-targeted tests.
- Do not claim unverified MCP Server, Agent Skills, Prometheus, Grafana, production traffic, user growth, performance percentages, or AI-generated-code percentages.
- Temporary builders, validators, portrait exports, and rendered PNGs belong under `tmp/pdfs/` and must be removed after final validation.
- Do not stage, commit, or push generated PDFs or temporary files.

---

### Task 1: Establish the artifact validation contract

**Files:**
- Create: `tmp/pdfs/validate_resume_capability.py`
- Test: `output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`

**Interfaces:**
- Consumes: final PDF path passed as `sys.argv[1]`.
- Produces: exit code `0` and a JSON summary when all structural and textual checks pass; a non-zero exit code with an assertion message otherwise.

- [ ] **Step 1: Write the validator before generating the PDF**

```python
import json
import os
import re
import sys

import fitz
import pdfplumber

path = sys.argv[1]
assert os.path.exists(path), f"missing output: {path}"

required = [
    "大模型应用开发工程师（具备 RAG / Agent 工程化开发经验）",
    "AI 应用开发",
    "检索与知识工程",
    "Python 后端开发",
    "工程化与 AI 编程",
    "能够独立完成文档导入、检索问答、工具调用、会话记忆和来源引用",
    "能够实现按用户和文档隔离的向量检索、跨文档知识关联",
    "能够完成异步 API、认证鉴权、缓存、事务处理及数据一致性保护",
    "Codex、Claude Code 与规范驱动流程",
    "多用户智能文档学习与知识问答平台",
    "新闻资讯聚合与用户行为后端系统",
    "837",
    "43 项",
]
forbidden = [
    "RAG / Agent 工程化方向",
    "精通",
    "MCP Server",
    "Agent Skills",
    "Prometheus",
    "Grafana",
    "90% 以上的代码由 AI 生成",
]

with pdfplumber.open(path) as pdf:
    assert len(pdf.pages) == 2, len(pdf.pages)
    text_by_page = [page.extract_text() or "" for page in pdf.pages]
full_text = "\n".join(text_by_page)
flat_text = re.sub(r"\s+", "", full_text)
for item in required:
    assert re.sub(r"\s+", "", item) in flat_text, f"missing required text: {item}"
for item in forbidden:
    assert re.sub(r"\s+", "", item) not in flat_text, f"forbidden text present: {item}"
assert "多用户智能文档学习与知识问答平台" in text_by_page[0]
assert sum("多用户智能文档学习与知识问答平台" in p for p in text_by_page) == 2

doc = fitz.open(path)
assert doc.page_count == 2
assert sum(len(page.get_links()) for page in doc) >= 1
portraits = [
    info
    for info in doc[0].get_image_info(xrefs=True)
    if info["bbox"][0] > 450 and info["bbox"][1] < 60
]
assert len(portraits) == 1, portraits
x0, y0, x1, y1 = portraits[0]["bbox"]
assert abs((x1 - x0) / (y1 - y0) - 368 / 516) < 0.01
summary = {
    "pages": doc.page_count,
    "links": sum(len(page.get_links()) for page in doc),
    "bytes": os.path.getsize(path),
    "portrait_bbox": [round(v, 2) for v in portraits[0]["bbox"]],
}
print(json.dumps(summary, ensure_ascii=False))
```

- [ ] **Step 2: Run the validator and confirm the new file does not exist yet**

Run:

```powershell
$py='C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py 'D:\python_self_agent\tmp\pdfs\validate_resume_capability.py' 'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf'
```

Expected: FAIL with `missing output`.

### Task 2: Build the two-page capability-focused resume

**Files:**
- Create: `tmp/pdfs/build_resume_capability.py`
- Read: `output/pdf/冯嘉文_简历_大模型应用开发工程师_面试优化版.pdf`
- Create: `tmp/pdfs/resume-capability-portrait.png`
- Create: `output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`

**Interfaces:**
- Consumes: source PDF path and output PDF path from `sys.argv[1:3]`.
- Produces: a two-page ReportLab PDF with searchable text and a URL annotation for `https://github.com/wanghaohao840-collab/-`.

- [ ] **Step 1: Implement common drawing helpers**

Create helpers with these exact signatures:

```python
def draw_wrapped(c, text, x, y, width, font, size, leading, color=INK):
    """Draw Chinese/Latin text with word-aware wrapping and return the next y."""

def draw_section(c, title, y):
    """Draw a light-blue full-width band with a 3 pt blue left accent."""

def draw_skill(c, label, text, y):
    """Draw a bold skill label and its capability description; return next y."""

def draw_bullet(c, text, y, width=CONTENT_W, size=8.8, leading=14.2):
    """Draw a blue bullet plus wrapped body text; return next y."""

def draw_footer(c, page_number):
    """Draw the thin footer rule and `page_number / 2`."""
```

Use these visual constants:

```python
PAGE_W, PAGE_H = A4
LEFT = 42.52
RIGHT = PAGE_W - 42.52
CONTENT_W = RIGHT - LEFT
NAVY = colors.HexColor("#174C7E")
BLUE = colors.HexColor("#2D6AA3")
INK = colors.HexColor("#20242A")
MUTED = colors.HexColor("#66717F")
PALE = colors.HexColor("#EDF4FB")
LINE = colors.HexColor("#C7D7E8")
WHITE = colors.white
```

Register `C:/Windows/Fonts/msyh.ttc` as `ResumeRegular` and `C:/Windows/Fonts/msyhbd.ttc` as `ResumeBold`. Use ReportLab's subset embedding so the final file stays below 1 MB.

- [ ] **Step 2: Extract and place the corrected portrait**

Use PyMuPDF to select the single page-one image whose bounding box starts after `x=450` and before `y=60`, extract it to `tmp/pdfs/resume-capability-portrait.png`, and draw it into a frame whose aspect ratio is `368 / 516`. The frame border must end at the image's right edge; do not stretch the image to a wider box.

- [ ] **Step 3: Draw page one content**

Use this exact content order:

```python
headline = "大模型应用开发工程师（具备 RAG / Agent 工程化开发经验）"
contact_1 = "电话：19566031070  ｜  邮箱：1127230940@qq.com"
contact_2 = "常居：珠海  ｜  2027 届本科  ｜  预计毕业：2027.06"
github_label = "GitHub / 项目源码：github.com/wanghaohao840-collab/-"
education = "2023.09 - 2027.06  韩山师范学院  电子信息科学与技术（本科）"
courses = "主修课程：Python 编程、数据结构、计算机网络、信号与系统、数字电路"
skills = [
    ("AI 应用开发", "掌握 RAG、Agent、Memory、Function Calling 与 Prompt Engineering，能够独立完成文档导入、检索问答、工具调用、会话记忆和来源引用等大模型应用链路。"),
    ("检索与知识工程", "熟悉 Qdrant、Hybrid Retrieval、MMR、Neo4j 与 GraphRAG，能够实现按用户和文档隔离的向量检索、跨文档知识关联、结果过滤及稳定引用。"),
    ("Python 后端开发", "熟悉 Python、FastAPI、SQLAlchemy、MySQL、SQLite 与 Redis，能够完成异步 API、认证鉴权、缓存、事务处理及数据一致性保护。"),
    ("工程化与 AI 编程", "熟悉 Docker Compose、pytest、Git 及 Linux / Windows 单节点部署运维；能够使用 Codex、Claude Code 与规范驱动流程完成需求拆解、实现审查、自动化验证、备份恢复和升级回滚。"),
]
project_intro = "面向文档学习中检索效率低、知识分散和缺少长期记忆的问题，独立构建覆盖导入、检索、问答、记忆、报告和部署运维的多用户 AI 应用。"
page_one_bullets = [
    "从 0 到 1 设计 UI → Assistant → Tool → Retrieval / Memory → Storage 分层架构，完成认证、文档管理、问答检索、学习记录与报告导出的产品闭环。",
    "构建 PDF、TXT、MD、DOCX 导入 Pipeline，采用语义段落切分、Chunk Overlap、稳定 Chunk ID 与页码元数据，使文档替换和回答引用可追溯。",
    "以用户 namespace + document_id 双重约束贯穿导入、检索、问答和删除链路，并结合 UUID 用户目录与会话级当前文档，阻断跨用户、跨文档数据串读。",
    "支持最多 10 篇文档的联合问答、对比分析与联合总结，通过意图识别、上下文预算和有界并发 map-reduce 提升长文档覆盖度。",
    "抽象 JSON / Qdrant 可切换 RAG 后端，引入 Hybrid Retrieval、MMR 与过滤检索，统一统计、删除、清空和错误脱敏契约。",
]
```

Draw the four metric cards as `4 种 / 文档格式`, `10 篇 / 联合问答`, `20 个 / 单批文件`, and `837 项 / 可收集测试`. Page one must end after at least the first three project bullets and no lower than `y=95`.

- [ ] **Step 4: Draw page two content**

Use this exact content:

```python
page_two_main_bullets = [
    "接入 Neo4j 构建文档图谱，将向量证据与 GraphRAG 上下文融合；通过跨文档规范实体与稳定 S-* / G-* 引用增强关联分析和答案可解释性，相关 43 项定向测试通过。",
    "设计持久化批量异步导入：单批最多 20 个文件、最多 4 个用户并行，任务状态写入 SQLite，支持阶段进度、分级退避重试和进程重启恢复。",
    "完善并发写协调、事务补偿、损坏数据隔离与恢复，提供 Docker Compose 及 Windows 单节点健康检查、冷备份、恢复演练和升级回滚。",
    "采用设计说明 → 任务包 → 实现 → 集成审查的规范驱动流程，配合 Codex、Claude Code、Git 与 pytest 分阶段验证，使 AI 辅助改动可审查、可回滚、可复现。",
]
backend_intro = "面向新闻资讯应用的用户、内容和行为数据管理需求，独立实现异步 API、身份认证、缓存与持久化链路。"
backend_bullets = [
    "按 Router、Schema、CRUD、Model、Cache、Utils 拆分职责，以 Pydantic 定义请求与响应模型，并通过 SQLAlchemy AsyncSession + aiomysql 构建异步数据访问层。",
    "采用 UUID Token、Passlib + bcrypt 实现注册登录、密码哈希、过期控制和资料维护，以联合唯一约束避免重复收藏。",
    "使用 Redis Cache-Aside 缓存新闻分类、列表、详情和相关推荐，并结合 MySQL 事务回滚、CORS 与统一异常响应保障一致性。",
]
advantages = [
    "具备从需求分析、架构设计、编码实现、自动化测试到部署运维的独立交付经验，主项目已形成可演示的完整产品闭环。",
    "当前仓库可收集 837 项自动化测试，GraphRAG 相关 43 项定向测试已通过；对数据隔离、故障恢复和变更验证有实际工程经验。",
    "CET-6，能够直接阅读英文官方文档和开源项目源码，并将新技术落实到可运行、可测试的项目中。",
]
```

Keep page two's last content baseline above `y=86`, then draw the footer.

- [ ] **Step 5: Save atomically**

Write first to `tmp/pdfs/resume-capability-output.tmp.pdf`, reopen it with pypdf, assert two pages, then use `os.replace()` to publish the final output path.

### Task 3: Generate and pass structural validation

**Files:**
- Read: `tmp/pdfs/build_resume_capability.py`
- Read: `tmp/pdfs/validate_resume_capability.py`
- Create: `output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`

**Interfaces:**
- Consumes: the builder and validator interfaces from Tasks 1-2.
- Produces: a final PDF that passes the validator without warnings.

- [ ] **Step 1: Run the builder**

```powershell
$py='C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py 'D:\python_self_agent\tmp\pdfs\build_resume_capability.py' 'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_面试优化版.pdf' 'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf'
```

Expected: exit code `0`, two-page output created, final size below `1,000,000` bytes.

- [ ] **Step 2: Run the validator**

```powershell
& $py 'D:\python_self_agent\tmp\pdfs\validate_resume_capability.py' 'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf'
```

Expected: exit code `0`; JSON reports `"pages": 2`, at least one link, and one portrait.

- [ ] **Step 3: Confirm earlier files are unchanged**

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath `
  'C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf', `
  'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师版.pdf', `
  'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_面试优化版.pdf'
```

Expected: original hash remains `3D08ADE212257AFF88ED1FE64DDE5A89F8F7C8C3F2F1B3023732808B9505C513`; current engineering version remains `352D200C78C5300C674D205DBC0254CAF451677A7F47C7D478EAD92F32D6DACD`; current interview-optimized version remains `DEFA4D31AF350EA5277A701094A5FDAB382C831FC570B0D40299828DD6BD4EE0`.

### Task 4: Render and visually inspect both pages

**Files:**
- Read: `output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`
- Create: `tmp/pdfs/resume-capability-qa-1.png`
- Create: `tmp/pdfs/resume-capability-qa-2.png`

**Interfaces:**
- Consumes: validated PDF from Task 3.
- Produces: two 180-DPI PNGs used only for visual QA.

- [ ] **Step 1: Render the final PDF**

```powershell
$pop='C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe'
& $pop -png -r 180 'D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf' 'D:\python_self_agent\tmp\pdfs\resume-capability-qa'
```

Expected: exactly two PNGs.

- [ ] **Step 2: Inspect page one**

Require all of the following:

- Portrait has no visible right-side frame gap and is not stretched.
- Four skill groups are readable and visually distinct.
- Main project title and at least three bullets appear on page one.
- No title, paragraph, metric card, or footer is clipped or overlapping.
- Bottom whitespace is balanced and not materially larger than page two's.

- [ ] **Step 3: Inspect page two**

Require all of the following:

- Main-project continuation is clearly labeled.
- Second project and evidence-based advantages are visually separated.
- Footer reads `2 / 2` completely.
- No line exceeds the content boundary and no isolated single-character line appears.

- [ ] **Step 4: Iterate if any defect is visible**

Adjust only font size, leading, section spacing, or the split point between page-one and page-two bullet arrays. Rebuild, rerun the validator, rerender both pages, and inspect the latest PNGs again. Do not deliver an earlier render.

### Task 5: Clean temporary artifacts and deliver

**Files:**
- Delete: `tmp/pdfs/build_resume_capability.py`
- Delete: `tmp/pdfs/validate_resume_capability.py`
- Delete: `tmp/pdfs/resume-capability-portrait.png`
- Delete: `tmp/pdfs/resume-capability-output.tmp.pdf`
- Delete: `tmp/pdfs/resume-capability-qa-1.png`
- Delete: `tmp/pdfs/resume-capability-qa-2.png`
- Preserve: `output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf`

**Interfaces:**
- Consumes: the final validated artifact and QA evidence.
- Produces: a clean workspace and one new user-facing PDF.

- [ ] **Step 1: Remove only the explicitly listed temporary files**

Resolve each path and verify it starts with `D:\python_self_agent\tmp\pdfs\` before deleting it. Do not recursively delete `tmp/` or any unrelated test directory.

- [ ] **Step 2: Reopen the final PDF in Codex**

Open `D:/python_self_agent/output/pdf/冯嘉文_简历_大模型应用开发工程师_能力增强版.pdf` in the right panel.

- [ ] **Step 3: Report the result**

State the four capability groups, the evidence-driven project changes, validation results, and preservation of earlier PDFs. Cite the final PDF exactly once with `purpose="output"`.

## Self-Review

- Spec coverage: Tasks 1-5 cover non-overwrite behavior, four skill groups, headline, evidence-based project language, two-page layout, GitHub link, portrait framing, searchable text, file size, rendering, and cleanup.
- Placeholder scan: clean; every action, command, expected result, and output path is explicit.
- Interface consistency: the builder consumes source/output paths; the validator consumes the output path; Task 3 produces the artifact consumed by Tasks 4-5.
