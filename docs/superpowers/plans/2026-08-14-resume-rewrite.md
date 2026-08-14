# 大模型应用开发工程师简历改写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改原始 PDF 的前提下，生成并验收一份两页“大模型应用开发工程师（RAG / Agent 工程化方向）”中文简历副本。

**Architecture:** 使用一个仅存在于 `tmp/pdfs/resume-rewrite/` 的 ReportLab 构建脚本，从原 PDF 提取个人信息和证件照，并用经仓库事实校验的项目文案重新排版。使用独立验证脚本检查页数、文本、链接、敏感文件未进入 Git 和基础版面边界，再通过 Poppler 渲染两页 PNG 进行人工视觉复核。

**Tech Stack:** Python 3、ReportLab 4.4.9、pdfplumber、pypdf 6.10.0、Pillow 12.3.0、Poppler、pytest。

## Global Constraints

- 原文件 `C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf` 只读，绝不覆盖。
- 最终文件为 `output/pdf/冯嘉文_简历_大模型应用开发工程师版.pdf`。
- 中间文件只放在 `tmp/pdfs/resume-rewrite/`，不提交个人信息、证件照或生成后的简历。
- PDF 恰好两页 A4，保留蓝白配色和证件照，第一页必须出现核心项目。
- 只使用原简历和当前仓库能够核实的事实，不写规划项、虚构指标或无证据技能。
- 测试数字写为“仓库收集 800+ 项自动化测试用例”；除非完整套件通过，否则不写成全部通过。
- 输出 PDF 必须可复制、可检索，GitHub 地址必须可点击或复制。

---

### Task 1: 建立隐私安全的内容与素材提取器

**Files:**
- Create: `tmp/pdfs/resume-rewrite/test_resume_builder.py`
- Create: `tmp/pdfs/resume-rewrite/build_resume.py`
- Create at runtime: `tmp/pdfs/resume-rewrite/portrait.jpg`

**Interfaces:**
- Consumes: 原始 PDF 路径和当前仓库 Git remote。
- Produces: `extract_identity(source_pdf: Path) -> dict[str, str]`、`extract_portrait(source_pdf: Path, output_path: Path) -> Path`、`repository_url(repo_root: Path) -> str`。

- [ ] **Step 1: 写提取器失败测试**

```python
from pathlib import Path

from build_resume import extract_identity, extract_portrait, repository_url


SOURCE = Path(r"C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf")
ROOT = Path(r"D:\python_self_agent")


def test_extract_identity_reads_required_fields():
    identity = extract_identity(SOURCE)
    assert identity["name"]
    assert identity["phone"].isdigit() and len(identity["phone"]) == 11
    assert "@" in identity["email"]
    assert identity["school"] == "韩山师范学院"
    assert identity["major"] == "电子信息科学与技术"
    assert identity["graduation"] == "2027.06"


def test_extract_portrait_writes_jpeg(tmp_path):
    target = tmp_path / "portrait.jpg"
    result = extract_portrait(SOURCE, target)
    assert result == target
    assert target.read_bytes().startswith(b"\xff\xd8")


def test_repository_url_removes_git_suffix():
    assert repository_url(ROOT) == "https://github.com/wanghaohao840-collab/-"
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
& 'C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -q tmp\pdfs\resume-rewrite\test_resume_builder.py
```

Expected: collection fails because `build_resume` does not exist.

- [ ] **Step 3: 实现最小提取器**

```python
import re
import subprocess
from io import BytesIO
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader


def _match(pattern: str, text: str, field: str) -> str:
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"missing source field: {field}")
    return match.group(1).strip()


def extract_identity(source_pdf: Path) -> dict[str, str]:
    with pdfplumber.open(source_pdf) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return {
        "name": _match(r"姓\s*名：([^\s]+)", text, "name"),
        "phone": _match(r"联系电话：\s*(\d{11})", text, "phone"),
        "email": _match(r"电子邮箱：\s*([^\s]+)", text, "email"),
        "location": _match(r"常居住地：\s*([^\s]+)", text, "location"),
        "school": _match(r"毕业院校：\s*([^\s]+)", text, "school"),
        "major": _match(r"专\s*业：\s*([^\n]+?)\s+在校时间", text, "major"),
        "graduation": _match(r"在校时间：\s*\d{4}\.\d{2}\s*-\s*(\d{4}\.\d{2})", text, "graduation"),
    }


def extract_portrait(source_pdf: Path, output_path: Path) -> Path:
    image_data = PdfReader(source_pdf).pages[0].images[0].data
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(BytesIO(image_data)).convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


def repository_url(repo_root: Path) -> str:
    value = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=repo_root, text=True
    ).strip()
    return value.removesuffix(".git")
```

- [ ] **Step 4: 运行提取器测试**

Run: 与 Step 2 相同。

Expected: `3 passed`。

---

### Task 2: 生成两页简历 PDF

**Files:**
- Modify: `tmp/pdfs/resume-rewrite/build_resume.py`
- Create at runtime: `output/pdf/冯嘉文_简历_大模型应用开发工程师版.pdf`

**Interfaces:**
- Consumes: Task 1 的身份信息、证件照和 GitHub URL。
- Produces: `build_resume(source_pdf: Path, repo_root: Path, output_pdf: Path) -> Path`。

- [ ] **Step 1: 增加生成结果测试**

```python
from pypdf import PdfReader

from build_resume import build_resume


def test_build_resume_creates_two_searchable_pages(tmp_path):
    output = tmp_path / "resume.pdf"
    build_resume(SOURCE, ROOT, output)
    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) == 2
    assert "大模型应用开发工程师（RAG / Agent 工程化方向）" in text
    assert "多用户智能文档学习助手" in text
    assert "GraphRAG" in text
    assert "单批最多 20 个文件" in text
    assert "800+ 项自动化测试用例" in text
    assert "规划方向" not in text
    assert "AutoGen" not in text
```

- [ ] **Step 2: 运行测试并确认失败**

Run: Task 1 Step 2 的 pytest 命令。

Expected: `ImportError` because `build_resume` is not defined.

- [ ] **Step 3: 实现生成器**

实现时使用以下固定结构，不增加额外板块：

```python
TARGET_ROLE = "大模型应用开发工程师（RAG / Agent 工程化方向）"
SKILLS = [
    ("AI 应用", "RAG｜Agent｜Function Calling｜Memory｜Prompt Engineering｜Qdrant｜Neo4j / GraphRAG｜OpenAI Compatible API"),
    ("后端开发", "Python｜FastAPI｜SQLAlchemy｜MySQL｜SQLite｜Redis"),
    ("工程实践", "Gradio｜Docker Compose｜pytest｜Git｜Linux｜Windows 单节点部署运维"),
]
PRIMARY_PROJECT = [
    "采用 UI → Assistant → Tool → Retrieval / Memory → Storage 分层架构，独立完成需求分析、系统设计、实现与验证。",
    "实现 PDF、TXT、MD、DOCX 导入，以用户 namespace + document_id 双重约束保障多用户、多文档检索和删除隔离。",
    "支持最多 10 篇文档的联合问答、对比分析与 map-reduce 联合总结，并提供页码级来源和稳定引用。",
    "抽象 JSON / Qdrant 可切换 RAG 后端，引入混合检索、MMR 与上下文预算控制，统一检索、统计和清理契约。",
    "接入 Neo4j 构建文档图谱，将向量证据与 GraphRAG 上下文融合，并通过规范实体支持跨文档关联。",
    "设计持久化批量异步导入：单批最多 20 个文件、最多 4 个用户并行，支持阶段进度、失败重试和重启恢复。",
    "完善数据损坏检测、隔离、备份恢复和错误脱敏；提供 Docker Compose 及 Windows 单节点备份、恢复演练与升级回滚。",
    "建立单元、契约、集成和验收测试体系，当前仓库可收集 800+ 项自动化测试用例。",
]
SECONDARY_PROJECT = [
    "按 Router、Schema、CRUD、Model、Cache 拆分职责，形成可扩展的异步后端分层架构。",
    "使用 FastAPI、SQLAlchemy AsyncSession 与 aiomysql 实现异步数据库访问和事务管理。",
    "采用 UUID Token、Passlib + bcrypt 与依赖注入实现注册登录、密码哈希、鉴权和过期控制。",
    "使用 Redis Cache-Aside 缓存新闻列表、详情和相关推荐，并通过 MySQL 持久化、事务回滚及统一异常响应保障一致性。",
]
ADVANTAGES = [
    "具备从需求分析、架构设计、编码实现、自动化测试到部署运维的独立交付经验。",
    "重视数据隔离、故障恢复、错误脱敏和可验证性，能从应用全生命周期处理工程问题。",
    "CET-6，可直接阅读英文技术文档和开源项目源码。",
]
```

注册 `C:\Windows\Fonts\msyh.ttc` 和 `msyhbd.ttc`，使用 ReportLab `BaseDocTemplate`、两个固定 `PageTemplate` 和单栏 `Frame` 构建两页 A4。第一页放头部、教育、技能和核心项目，第二页续接核心项目并放第二项目与个人优势；通过显式 `PageBreak` 保证页数稳定。使用 `KeepTogether` 防止标题与首条内容分离，GitHub URL 使用 `<link href="...">...</link>` 生成可点击链接。

- [ ] **Step 4: 运行生成测试**

Run: Task 1 Step 2 的 pytest 命令。

Expected: `4 passed`。

- [ ] **Step 5: 生成正式副本**

```powershell
$sourceHashBefore = (Get-FileHash -Algorithm SHA256 'C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf').Hash
& 'C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tmp\pdfs\resume-rewrite\build_resume.py
$sourceHashAfter = (Get-FileHash -Algorithm SHA256 'C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf').Hash
if ($sourceHashBefore -ne $sourceHashAfter) { throw 'source PDF changed' }
```

Expected: 创建 `output/pdf/冯嘉文_简历_大模型应用开发工程师版.pdf`，原 PDF 的大小和修改时间保持不变。

---

### Task 3: 自动校验、渲染和视觉修正

**Files:**
- Create: `tmp/pdfs/resume-rewrite/validate_resume.py`
- Create at runtime: `tmp/pdfs/resume-rewrite/rendered/page-1.png`
- Create at runtime: `tmp/pdfs/resume-rewrite/rendered/page-2.png`
- Modify when needed: `tmp/pdfs/resume-rewrite/build_resume.py`

**Interfaces:**
- Consumes: Task 2 的最终 PDF。
- Produces: 自动校验结果、两张页面 PNG 和最终修正版 PDF。

- [ ] **Step 1: 实现自动校验器**

```python
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


OUTPUT = Path(r"D:\python_self_agent\output\pdf\冯嘉文_简历_大模型应用开发工程师版.pdf")
SOURCE = Path(r"C:\Users\11272\OneDrive\桌面\冯嘉文_简历.pdf")


reader = PdfReader(OUTPUT)
assert len(reader.pages) == 2
text = "\n".join(page.extract_text() or "" for page in reader.pages)
for required in [
    "大模型应用开发工程师（RAG / Agent 工程化方向）",
    "多用户智能文档学习助手",
    "GraphRAG",
    "FastAPI",
    "CET-6",
]:
    assert required in text, required
for forbidden in ["规划方向", "AutoGen", "LangGraph", "CNN", "RNN"]:
    assert forbidden not in text, forbidden
with pdfplumber.open(OUTPUT) as pdf:
    for page in pdf.pages:
        assert page.extract_text(), "page has no searchable text"
assert OUTPUT.resolve() != SOURCE.resolve()
print("resume validation passed")
```

- [ ] **Step 2: 运行自动校验**

```powershell
& 'C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tmp\pdfs\resume-rewrite\validate_resume.py
```

Expected: `resume validation passed`。

- [ ] **Step 3: 使用 Poppler 渲染**

```powershell
New-Item -ItemType Directory -Force 'tmp\pdfs\resume-rewrite\rendered' | Out-Null
& 'C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe' -png -r 180 'output\pdf\冯嘉文_简历_大模型应用开发工程师版.pdf' 'tmp\pdfs\resume-rewrite\rendered\page'
```

Expected: exactly `page-1.png` and `page-2.png`。

- [ ] **Step 4: 逐页视觉检查并修正**

检查两张 PNG：无裁切、重叠、乱码、黑块、孤立标题和大面积空白；证件照比例正确；两页的标题、线条、项目符号、页边距和页脚一致。发现问题时只调整字号、行距、段前后距或分页位置，重新生成、自动校验和渲染，直到零视觉缺陷。

- [ ] **Step 5: 最终隐私与 Git 检查**

```powershell
git status --short
git check-ignore -v 'output/pdf/冯嘉文_简历_大模型应用开发工程师版.pdf'
git diff --cached --name-only
```

Expected: 最终 PDF 被 `*.pdf` 规则忽略，照片和临时构建文件不在 Git 暂存区。

- [ ] **Step 6: 删除含个人信息的临时素材**

```powershell
Remove-Item -LiteralPath 'D:\python_self_agent\tmp\pdfs\resume-rewrite\portrait.jpg' -Force
```

Expected: 只交付最终 PDF，不提交或保留临时证件照副本；保留构建和验证脚本以便本轮继续修正，任务结束后可一并清理。
