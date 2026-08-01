# README and Current Feature Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite README to match the implemented feature set, validate the complete worktree, and publish it to the current Git branch.

**Architecture:** Documentation remains at the repository root and describes the existing UI → Assistant → Tool → Memory/RAG/Storage dependency flow. No production behavior is changed by this task; the release commit packages the already implemented code, tests, workflow documents, and the corrected README.

**Tech Stack:** Python, Gradio, SQLite, JSON, Qdrant, pytest, Markdown, Git

## Global Constraints

- Treat current code, tests, configuration, and runtime behavior as authoritative.
- Preserve user and `document_id` isolation boundaries.
- Do not stage `.env`, credentials, runtime data, uploads, caches, databases, or generated reports.
- Push the current branch `codex/multi-document-rag-query`; do not rewrite `main`.

---

### Task 1: Rewrite the repository README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Current behavior exposed by `ui/gradio_app.py`, `assistants/pdf_learning_assistant.py`, `app/`, and `hello_agents/`.
- Produces: A current installation, configuration, usage, architecture, data-layout, and validation guide.

- [ ] **Step 1: Replace stale feature and roadmap sections**

Document multi-user authentication, per-user isolation, multi-document QA modes, JSON/Qdrant backends, migration, recovery, report snapshots, and the actual supported formats.

- [ ] **Step 2: Document configuration and startup**

Include `LLM_*`, `RAG_BACKEND`, `QDRANT_*`, optional context-budget variables, `PDF_ASSISTANT_DATA_DIR`, dependency installation, and `ui/gradio_app.py` startup.

- [ ] **Step 3: Document implementation layout and invariants**

Describe the UI, application service, assistant, tool, RAG/storage, Memory, examples, tests, and runtime-data directories. State that Neo4j is not yet a real backend.

- [ ] **Step 4: Check README claims against source**

Run:

```powershell
rg -n "RAG_BACKEND|QDRANT_URL|PDF_ASSISTANT_DATA_DIR|MAX_SELECTED_DOCUMENTS" ui app assistants hello_agents
```

Expected: Every documented configuration or limit has a corresponding implementation reference.

### Task 2: Validate and publish the confirmed worktree

**Files:**
- Include: all tracked and untracked source, test, design, workflow, and instruction files confirmed by the user.
- Exclude: ignored secrets and runtime artifacts.

**Interfaces:**
- Consumes: The complete current worktree.
- Produces: One reviewed commit pushed to `origin/codex/multi-document-rag-query`.

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
$env:TEMP=(Resolve-Path '.runtime\pytest-tmp').Path
$env:TMP=$env:TEMP
D:\Anaconda\python.exe -m pytest -q
```

Expected: all collected non-environment-gated tests pass; Qdrant integration tests may skip when no external service is configured.

- [ ] **Step 2: Review the final diff and secret exposure**

Run:

```powershell
git status -sb
git diff --check
git diff --stat
git ls-files --others --exclude-standard
```

Expected: only confirmed repository content appears; no `.env`, runtime data, uploaded documents, databases, or generated reports are present.

- [ ] **Step 3: Stage and inspect**

Run:

```powershell
git add -A
git status --short
git diff --cached --check
git diff --cached --stat
```

Expected: the staged set contains the confirmed complete feature implementation and documentation without ignored artifacts.

- [ ] **Step 4: Commit**

Run:

```powershell
git commit -m "feat: publish multi-user multi-document RAG assistant"
```

Expected: one commit is created on `codex/multi-document-rag-query`.

- [ ] **Step 5: Push**

Run:

```powershell
git push -u origin codex/multi-document-rag-query
```

Expected: the remote feature branch is created or updated successfully.
