# Hello-Agents Resume Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the confirmed Datawhale Hello-Agents study-and-code-practice evidence to the experience library, create five enhanced JD matrices, and generate five new role-specific two-page PDF resumes without modifying existing artifacts.

**Architecture:** Treat `output/resume_workspace/经历库.md` as the canonical evidence source. Create role-specific matrix copies that consume the new evidence, then use one temporary ReportLab builder driven by five role configurations to generate consistent PDFs with different evidence emphasis. Validate every PDF structurally with pypdf and visually from 180 DPI Poppler renders.

**Tech Stack:** Markdown, Python 3, ReportLab, pypdf, Poppler `pdftoppm`, Microsoft YaHei fonts.

## Global Constraints

- Learning period is exactly `2026.05.10 - 2026.06.25`.
- The candidate systematically read and understood all Hello-Agents chapters and ran/debugged the companion code.
- The candidate ran/debugged part of the Chapter 11 SFT/GRPO example code.
- Do not claim repository contribution, independent training design, complete reproduction, training metrics, production Agentic-RL, multimodal training, papers, or business algorithm outcomes.
- Generate five new matrix files and five new PDF files; do not overwrite existing matrices or resumes.
- Keep each PDF at two A4 pages with the existing blue/white visual language, photo, contact details, and clickable GitHub link.
- Preserve the news backend project as a paused learning project.

---

## File Structure

- Modify `output/resume_workspace/经历库.md`: add the canonical Hello-Agents learning-practice evidence and its expression ledger.
- Create `output/resume_workspace/JD-证据匹配矩阵_物联网应用软件开发_HelloAgents学习增强版.md`: IoT/Python/pre-research evidence mapping.
- Create `output/resume_workspace/JD-证据匹配矩阵_小红书跨境交易AI研发_HelloAgents学习增强版.md`: AI application and engineering evidence mapping.
- Create `output/resume_workspace/JD-证据匹配矩阵_小红书AI电商研发_HelloAgents学习增强版.md`: AI commerce development evidence mapping.
- Create `output/resume_workspace/JD-证据匹配矩阵_小红书C端Agent算法研发_HelloAgents学习增强版.md`: Agent framework, context, evaluation, and limited SFT/GRPO practice mapping.
- Create `output/resume_workspace/JD-证据匹配矩阵_小红书多模态统一模型与创作Agent_HelloAgents学习增强版.md`: upper-layer Agent and limited Agentic-RL practice mapping while preserving multimodal gaps.
- Create temporary `tmp/pdfs/build_hello_agents_enhanced_resumes.py`: shared renderer and five role configurations; delete after validation.
- Create five PDFs under `output/pdf/`, each ending in `_HelloAgents学习增强版.pdf`.

### Task 1: Canonical Learning Evidence

**Files:**
- Modify: `output/resume_workspace/经历库.md`

**Interfaces:**
- Consumes: user-confirmed study scope and the official Hello-Agents chapter structure.
- Produces: one evidence-library section with facts, transferable skills, and an allowed/prohibited expression ledger used by all later tasks.

- [ ] **Step 1: Insert the experience index entry**

Add `Datawhale Hello-Agents 智能体系统学习实践` with nature `开源课程学习实践`, status `已完成系统学习与代码调试`, dates `2026.05.10 - 2026.06.25`.

- [ ] **Step 2: Add detailed evidence**

Record these exact evidence groups: classic paradigms `ReAct / Plan-and-Solve / Reflection`; framework practice `AutoGen / AgentScope / CAMEL / LangGraph / HelloAgents`; advanced topics `Memory / RAG / Context Engineering / MCP / A2A / ANP / Agent Evaluation`; and `部分 SFT/GRPO 示例代码运行与调试`.

- [ ] **Step 3: Add the expression ledger**

Allow “系统学习、运行、调试、理解、示例实践”. Prohibit “贡献者、独立研发、完整复现、训练指标、生产落地、论文”.

- [ ] **Step 4: Validate the evidence text**

Run:

```powershell
rg -n "Hello-Agents|2026\.05\.10|SFT|GRPO|贡献者|独立研发|训练指标" output/resume_workspace/经历库.md
```

Expected: the learning section contains the confirmed facts and explicitly scoped prohibited claims; no claim says the candidate contributed to the repository.

### Task 2: Five Enhanced JD Matrices

**Files:**
- Create: `output/resume_workspace/JD-证据匹配矩阵_物联网应用软件开发_HelloAgents学习增强版.md`
- Create: `output/resume_workspace/JD-证据匹配矩阵_小红书跨境交易AI研发_HelloAgents学习增强版.md`
- Create: `output/resume_workspace/JD-证据匹配矩阵_小红书AI电商研发_HelloAgents学习增强版.md`
- Create: `output/resume_workspace/JD-证据匹配矩阵_小红书C端Agent算法研发_HelloAgents学习增强版.md`
- Create: `output/resume_workspace/JD-证据匹配矩阵_小红书多模态统一模型与创作Agent_HelloAgents学习增强版.md`

**Interfaces:**
- Consumes: Task 1 evidence section and the five original matrices.
- Produces: five role-specific matching decisions and PDF rewrite instructions.

- [ ] **Step 1: Copy factual baselines without overwriting originals**

For each original matrix, create a new suffixed file and preserve its existing direct, adjacent, and missing evidence classifications.

- [ ] **Step 2: Apply IoT weighting**

Add evidence for Python code reading/debugging, technical pre-research, documentation study, and framework comparison. Keep C/C++ and IoT domain experience as gaps. Use one compressed resume insertion.

- [ ] **Step 3: Apply cross-border and AI-commerce weighting**

Add Agent paradigms, framework selection, Context/Memory, protocols, evaluation, and AI-assisted engineering judgment. Keep commerce, payment, finance, distributed production, and real business metrics as gaps. Use a compact learning-practice subsection.

- [ ] **Step 4: Apply C-end Agent weighting**

Upgrade “Agent Harness absent” to “systematic framework study and code debugging”. Upgrade “RL absent” to “partial SFT/GRPO tutorial-code practice”; retain missing independent algorithm R&D, training design, metrics, and papers. Use a prominent learning-practice subsection.

- [ ] **Step 5: Apply multimodal Agent weighting**

Add upper-layer planning/tool use, framework, context, protocol, evaluation, and partial Agentic-RL example practice. Retain multimodal foundation-model, vision generation, MoE, independent RL research, and paper gaps. Use a prominent but explicitly scoped learning-practice subsection.

- [ ] **Step 6: Validate all matrices**

Run:

```powershell
rg -n "Hello-Agents|SFT|GRPO|缺口|边界" output/resume_workspace/*HelloAgents学习增强版.md
```

Expected: five files match; each contains role-specific use of the new evidence and retains unsupported-skill gaps.

### Task 3: Shared PDF Builder and Five Resume Configurations

**Files:**
- Create: `tmp/pdfs/build_hello_agents_enhanced_resumes.py`
- Create: `output/pdf/冯嘉文_简历_物联网应用软件开发工程师_JD定向版_HelloAgents学习增强版.pdf`
- Create: `output/pdf/冯嘉文_简历_小红书跨境交易_AI应用研发定向版_HelloAgents学习增强版.pdf`
- Create: `output/pdf/冯嘉文_简历_小红书AI电商研发_JD定向版_HelloAgents学习增强版.pdf`
- Create: `output/pdf/冯嘉文_简历_小红书C端Agent研发_JD定向版_HelloAgents学习增强版.pdf`
- Create: `output/pdf/冯嘉文_简历_小红书多模态创作Agent_JD定向版_HelloAgents学习增强版.pdf`

**Interfaces:**
- Consumes: Task 1 evidence, Task 2 rewrite strategies, existing source PDFs for photo/contact/layout reference.
- Produces: five two-page A4 PDFs with a common renderer and role-specific content dictionaries.

- [ ] **Step 1: Build common visual components**

Implement Microsoft YaHei font registration, header/photo block, blue section bars, skill table, project header, bullet renderer, footer, clickable GitHub link, and A4 document settings in the temporary builder.

- [ ] **Step 2: Define the shared learning-practice facts**

Use exact dates and a common base stating systematic study, code execution/debugging, and partial SFT/GRPO example practice. Do not include metrics or contribution claims.

- [ ] **Step 3: Define five role configurations**

IoT: one compact line emphasizing Python debugging, technical documentation, and pre-research.

Cross-border/AI commerce: a compact subsection emphasizing Agent paradigms, frameworks, Context/Memory, protocols, evaluation, and AI engineering judgment.

C-end Agent: a prominent subsection with ReAct, Plan-and-Solve, Reflection, LangGraph/HelloAgents, Memory/Context/protocol/evaluation, and partial SFT/GRPO example debugging.

Multimodal Agent: the same factual scope, emphasizing upper-layer planning/tool orchestration and limited Agentic-RL learning while avoiding multimodal-model claims.

- [ ] **Step 4: Generate the PDFs**

Run:

```powershell
& 'C:\Users\11272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'D:\python_self_agent\tmp\pdfs\build_hello_agents_enhanced_resumes.py'
```

Expected: five new PDF files, each with exactly two pages.

### Task 4: Structural and Visual Validation

**Files:**
- Inspect: the five new PDFs.
- Create temporarily: `tmp/pdfs/hello-agents-enhanced-qa/<role>/page-1.png` and `page-2.png`.

**Interfaces:**
- Consumes: Task 3 PDFs.
- Produces: verified artifacts and a final QA summary.

- [ ] **Step 1: Run structural validation**

Use pypdf to assert for each PDF: `len(reader.pages) == 2`, `reader.is_encrypted is False`, A4 page dimensions, and one GitHub URI annotation.

- [ ] **Step 2: Render all pages at 180 DPI**

Run Poppler `pdftoppm -png -r 180` into a dedicated role directory for each PDF.

- [ ] **Step 3: Inspect all ten page images**

Reject any artifact with clipped text, overlapping sections, unreadable font size, photo white strip, hanging section header, abnormal footer, or an obvious blank block. Adjust only the affected role configuration and rerender all pages for that PDF.

- [ ] **Step 4: Scan unsupported claims**

Extract PDF text where possible and inspect the source configuration for these prohibited claims: `开源贡献者`, `独立设计奖励函数`, `完整复现`, `训练效果提升`, `生产级强化学习`, `多模态模型训练`, `顶会论文`.

Expected: no prohibited claim appears.

- [ ] **Step 5: Record hashes and clean intermediates**

Compute SHA256 and byte size for each final PDF. Delete the temporary builder, extracted photo files, and all QA render directories using exact validated paths; preserve all final PDFs and matrices.

## Self-Review Result

- Spec coverage: all evidence, five matrices, five PDFs, non-overwrite rules, role weighting, and QA requirements have tasks.
- Placeholder scan: no placeholder or deferred implementation language remains.
- Interface consistency: Task 1 supplies canonical evidence, Task 2 supplies rewrite strategy, Task 3 generates artifacts, and Task 4 validates the exact outputs.
