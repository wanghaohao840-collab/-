# 学习计划、间隔复习、知识卡片与练习题 MVP 设计

## 1. 目标

在现有智能文档学习助手中增加一个按用户、按文档隔离的学习闭环：

1. 用户围绕当前单一文档创建半自动学习计划；
2. 系统生成每日任务并记录完成进度；
3. 用户从当前文档生成可追溯的知识卡片；
4. 用户从当前文档生成选择题和简答题；
5. 卡片自评和练习结果通过 SM-2 计算下次复习时间；
6. Gradio 提供统一的“学习中心”入口。

本功能是 MVP，不改变现有文档问答、笔记、报告、Memory 或 RAG 的既有语义。

## 2. 已确认的产品决策

- 生成范围：仅当前选中的单一文档。
- 计划创建：用户填写计划名称、目标日期和每日学习分钟数，系统自动生成每日任务。
- 判分方式：选择题自动判分；简答题展示参考答案后由用户自评。
- 来源要求：卡片和练习必须保存后端验证过的来源。
- 持久化：每位用户使用独立的 `learning.json`。
- 界面：现有顶层标签之后增加一个“学习中心”，内部包含“今日任务”“学习计划”“知识卡片”“练习题”四个子页。

## 3. 范围

### 3.1 MVP 包含

- 创建与查看文档学习计划；
- 自动生成每日任务；
- 标记任务完成或恢复未完成；
- 查看计划完成率；
- 生成、查看和复习知识卡片；
- 生成和完成选择题；
- 生成、作答和自评简答题；
- SM-2 到期调度；
- 查看当前文档的今日任务与到期复习项；
- 按用户和 `document_id` 隔离数据；
- 删除文档时级联清理学习数据；
- 原子 JSON 持久化和损坏数据失败关闭。

### 3.2 MVP 不包含

- 系统通知、邮件提醒或日历提醒；
- 学习计划编辑、拖拽重排或模板；
- 知识卡片和练习题的人工编辑；
- 跨文档生成；
- LLM 自动批改简答题；
- 遗忘曲线图、复杂掌握度仪表盘或排行榜；
- 自动打开原 PDF 的指定页；
- 将 `learning.json` 迁移到 SQLite、Qdrant 或 Neo4j；
- 为 `learning.json` 增加新的备份恢复界面。

## 4. 架构与依赖方向

保持现有单向依赖：

```text
Gradio UI
  → PDFLearningAssistant
    → LearningService
      → LearningRepository
        → learning.json
```

生成链路额外依赖现有 RAG 和 LLM，但不允许底层反向导入 UI、Assistant 或 Tool：

```text
LearningGenerationService
  → RAG 检索接口
  → LLM chat 接口
```

职责划分：

- `app/learning_repository.py`
  - 校验 `learning.json` 根结构和记录类型；
  - 原子加载、保存和基于最新快照的更新；
  - 按 `document_id` 级联删除；
  - 文件损坏时抛出专用异常，不静默重置。
- `app/learning.py`
  - 创建计划与每日任务；
  - 更新任务完成状态；
  - 实现 SM-2；
  - 记录卡片/题目评分；
  - 查询当前文档的计划、今日任务和到期复习项。
- `app/learning_generation.py`
  - 从当前文档检索来源片段；
  - 构造卡片或练习题生成提示；
  - 解析和校验结构化 JSON；
  - 校验来源、数量和重复问题；
  - 返回完整且已验证的领域草稿，不直接写文件。
- `app/runtime.py`
  - 为每个 `UserRuntime` 装配共享的 `LearningRepository`、`LearningService` 和生成服务；
  - 使用现有每用户共享 `RLock`。
- `assistants/pdf_learning_assistant.py`
  - 暴露 UI 所需的学习用例；
  - 只允许使用当前用户拥有的当前单一文档；
  - 将文档删除和学习数据清理串联。
- `ui/gradio_app.py`
  - 提供登录态处理函数；
  - 增加“学习中心”及四个内部子页。

## 5. 持久化模型

每位用户的数据文件位于其现有用户根目录下：

```text
data/users/<user_id>/learning.json
```

根结构：

```json
{
  "version": 1,
  "plans": [],
  "cards": [],
  "exercises": [],
  "review_logs": []
}
```

所有 ID 使用随机 UUID。所有时间点使用带时区的 UTC ISO 8601 字符串；计划和任务日期使用 `YYYY-MM-DD`。界面把目标日期解释为应用所在时区的自然日。

### 5.1 学习计划

```json
{
  "id": "uuid",
  "document_id": "doc-id",
  "document_name": "guide.pdf",
  "title": "RAG 入门计划",
  "target_date": "2026-08-12",
  "daily_minutes": 30,
  "status": "active",
  "tasks": [
    {
      "id": "uuid",
      "due_date": "2026-07-29",
      "phase": "reading",
      "title": "阅读并梳理当前文档重点",
      "duration_minutes": 30,
      "completed": false,
      "completed_at": null
    }
  ],
  "created_at": "2026-07-29T08:00:00+00:00",
  "updated_at": "2026-07-29T08:00:00+00:00"
}
```

`status` 只能是 `active` 或 `completed`。全部任务完成时自动变为 `completed`；任一任务恢复未完成时自动变回 `active`。

### 5.2 来源

卡片和练习共用来源结构：

```json
{
  "chunk_id": "chunk-id",
  "document_id": "doc-id",
  "document_name": "guide.pdf",
  "page_number": 12,
  "source_position": null,
  "preview": "最多 240 个字符的原文预览"
}
```

- PDF 使用 `page_number`；
- 非 PDF 使用 RAG 返回的稳定片段位置写入 `source_position`；
- 缺少稳定位置时允许 `source_position` 为 `null`，但 `chunk_id`、`document_id` 和文档名仍为必填；
- 来源由后端根据本次真实检索结果构造，不能直接采用 LLM 自报的文件名、页码或预览。

### 5.3 调度状态

卡片和练习共用调度结构：

```json
{
  "repetitions": 0,
  "interval_days": 0,
  "ease_factor": 2.5,
  "due_at": "2026-07-29T08:00:00+00:00",
  "last_reviewed_at": null,
  "last_grade": null
}
```

新生成项目立即到期，因此初始 `due_at` 等于创建时间。

### 5.4 知识卡片

```json
{
  "id": "uuid",
  "document_id": "doc-id",
  "document_name": "guide.pdf",
  "front": "检索增强生成解决了什么问题？",
  "back": "它通过检索外部资料为模型提供可追溯上下文。",
  "sources": [],
  "schedule": {},
  "created_at": "2026-07-29T08:00:00+00:00",
  "updated_at": "2026-07-29T08:00:00+00:00"
}
```

### 5.5 练习题

选择题：

```json
{
  "id": "uuid",
  "document_id": "doc-id",
  "document_name": "guide.pdf",
  "type": "multiple_choice",
  "prompt": "以下哪项最准确？",
  "options": ["A", "B", "C", "D"],
  "correct_index": 1,
  "reference_answer": null,
  "key_points": [],
  "explanation": "B 与原文定义一致。",
  "sources": [],
  "schedule": {},
  "created_at": "2026-07-29T08:00:00+00:00",
  "updated_at": "2026-07-29T08:00:00+00:00"
}
```

简答题：

```json
{
  "id": "uuid",
  "document_id": "doc-id",
  "document_name": "guide.pdf",
  "type": "short_answer",
  "prompt": "简述 RAG 的基本流程。",
  "options": [],
  "correct_index": null,
  "reference_answer": "先检索，再构造上下文，最后生成回答。",
  "key_points": ["检索", "上下文", "生成"],
  "explanation": null,
  "sources": [],
  "schedule": {},
  "created_at": "2026-07-29T08:00:00+00:00",
  "updated_at": "2026-07-29T08:00:00+00:00"
}
```

### 5.6 复习日志

```json
{
  "id": "uuid",
  "item_type": "card",
  "item_id": "uuid",
  "document_id": "doc-id",
  "grade": 5,
  "result": "mastered",
  "user_answer": null,
  "reviewed_at": "2026-07-29T08:10:00+00:00",
  "previous_due_at": "2026-07-29T08:00:00+00:00",
  "next_due_at": "2026-07-30T08:10:00+00:00"
}
```

`item_type` 只能是 `card` 或 `exercise`。简答题可以保存用户本次输入；卡片和选择题的 `user_answer` 为 `null` 或选择题选项索引。

`result` 只能是 `forgot`、`fuzzy`、`mastered`、`incorrect` 或 `correct`，并且必须与评分入口一致。

## 6. 学习计划规则

创建计划时要求：

- 已登录；
- 当前恰好选择一个仍存在且属于当前用户的文档；
- 计划名称去除首尾空白后非空，最大 100 个字符；
- 目标日期从当天起计算，周期为 1–365 天；
- `daily_minutes` 为 5–480 的整数。

每天生成一条任务，`duration_minutes` 等于用户输入的每日分钟数。

对于 4 天及以上的计划，以任务所在周期位置分配阶段：

- 前 25%：`reading`，阅读与梳理；
- 接下来 30%：`cards`，生成或复习知识卡片；
- 接下来 25%：`exercises`，完成练习题；
- 最后 20%：`review`，间隔复习、错题回顾与总结。

阶段按累计比例切分，并保证四个阶段各至少有一条任务。剩余天数按上述比例的最大余数法分配，顺序保持 `reading → cards → exercises → review`。

短计划采用明确规则：

- 1 天：一条“综合学习、练习与复习”任务，阶段为 `review`；
- 2 天：`reading`、`review`；
- 3 天：`reading`、`cards`、`review`。

过期但未完成的任务保留。完成率为 `completed_tasks / total_tasks`，空计划不允许创建。

## 7. SM-2 与判分

评分映射：

- 卡片和简答题“不会” → `1`；
- 卡片和简答题“模糊” → `3`；
- 卡片和简答题“掌握” → `5`；
- 选择题答错 → `1`；
- 选择题答对 → `4`。

更新规则：

```text
ease_factor =
  max(
    1.3,
    old_ease_factor
      + 0.1
      - (5 - grade) * (0.08 + (5 - grade) * 0.02)
  )
```

- `grade < 3`：
  - `repetitions = 0`
  - `interval_days = 1`
- `grade >= 3` 且此前 `repetitions == 0`：
  - `repetitions = 1`
  - `interval_days = 1`
- `grade >= 3` 且此前 `repetitions == 1`：
  - `repetitions = 2`
  - `interval_days = 6`
- `grade >= 3` 且此前 `repetitions >= 2`：
  - `repetitions += 1`
  - `interval_days = max(1, round(old_interval_days * new_ease_factor))`

`due_at = reviewed_at + interval_days`。计算、调度状态更新和日志追加必须在同一次原子学习数据更新中完成。

到期项满足 `due_at <= now`，仅返回当前文档的卡片和练习，按 `due_at`、创建时间、ID 稳定排序。

## 8. 生成规则

### 8.1 用户输入

- 当前文档：必须恰好一个；
- 数量：`3`、`5` 或 `10`，默认 `5`；
- 练习类型：`multiple_choice`、`short_answer` 或 `mixed`，默认 `mixed`。

`mixed` 的分配规则：

- 偶数：两类各一半；
- 奇数：选择题比简答题多一题。

### 8.2 生成流程

1. 在短暂用户锁内确认文档仍存在并记录稳定的 `document_id` 和文档名；
2. 释放锁；
3. 通过现有 RAG 查询/结果契约检索当前文档片段，不直接绑定 JSON 或 Qdrant 的内部存储；
4. 对 `chunk_id` 去重，保留来源元数据；
5. 把带有临时来源标识的片段交给 LLM；
6. 解析严格 JSON；
7. 校验整个批次；
8. 若只是 JSON 包裹或结构错误，允许进行一次有界的结构修复调用；
9. 在短暂用户锁内重新确认文档仍存在；
10. 与现有问题去重；
11. 由 `LearningService` 将整批结果原子写入。

生成期间不持有用户锁。若文档在生成期间被删除，整批结果丢弃且不写入。

### 8.3 卡片校验

- 数量必须精确匹配请求数量；
- `front` 和 `back` 去除首尾空白后非空；
- 每张卡片至少引用一个本次检索得到的临时来源标识；
- 同一批次及当前文档既有卡片中，归一化后的 `front` 不得重复。

归一化规则为 Unicode NFKC、转小写、合并连续空白、去除首尾空白。

### 8.4 选择题校验

- 必须恰好有 4 个非空且归一化后互不相同的选项；
- `correct_index` 必须是 `0–3` 的整数；
- `prompt` 和 `explanation` 非空；
- 至少引用一个本次检索得到的来源。

### 8.5 简答题校验

- `prompt`、`reference_answer` 非空；
- `key_points` 为 1–8 个非空字符串；
- 至少引用一个本次检索得到的来源。

同一批次及当前文档既有练习中，归一化后的 `prompt` 不得重复。

### 8.6 失败语义

以下情况不修改已有学习数据：

- 当前未选择恰好一个文档；
- 文档不存在或不属于当前用户；
- 无检索结果；
- LLM 未配置或调用失败；
- 首次解析和一次结构修复后仍不是合法 JSON；
- 数量、类型、字段、选项或来源校验失败；
- 提交前文档已被删除；
- 原子持久化失败。

界面显示稳定、可操作的错误信息，不显示 API Key、凭据或无关绝对路径。

## 9. UI 设计

新增一个顶层标签“8. 学习中心”，内部使用四个子标签。

### 9.1 公共文档选择

- 使用单选文档下拉框；
- 选项来自当前用户的文档历史；
- 刷新后保留仍然有效的当前选择；
- 所有学习中心操作都使用该单一文档，不复用问答页的多选语义。

### 9.2 今日任务

显示：

- 当前计划完成率；
- 当天计划任务数；
- 当前文档到期复习数；
- 当天和过期未完成任务列表；
- 到期卡片/练习列表。

提供：

- 刷新；
- 选中任务后标记完成或恢复未完成。

### 9.3 学习计划

输入：

- 计划名称；
- 目标日期；
- 每日学习分钟数。

输出：

- 当前文档计划列表；
- 选中计划的状态、完成率和任务表；
- 创建结果；
- 任务完成状态更新结果。

### 9.4 知识卡片

提供：

- 数量选择 `3 / 5 / 10`；
- “生成卡片”按钮；
- 当前文档卡片选择；
- 卡片正面；
- “显示答案与来源”；
- “不会 / 模糊 / 掌握”三个评分按钮；
- 更新后的下次复习时间。

评分按钮在答案展示前不应生效。

### 9.5 练习题

提供：

- 题型选择“混合 / 选择题 / 简答题”；
- 数量选择 `3 / 5 / 10`；
- “生成练习”按钮；
- 当前文档题目选择。

选择题流程：

1. 选择一个选项；
2. 提交；
3. 自动显示正确/错误、解析、来源和下次复习时间。

简答题流程：

1. 输入回答；
2. 显示参考答案、关键点和来源；
3. 选择“不会 / 模糊 / 掌握”；
4. 显示下次复习时间。

简答题在参考答案展示前不能评分。

## 10. 数据隔离、并发与删除

### 10.1 用户隔离

- UI 只通过会话令牌取得当前 `UserRuntime`；
- 学习服务不接受 UI 任意指定 `user_id`；
- 文件路径由当前运行时的用户根目录产生；
- 所有查询同时过滤当前用户文件和 `document_id`；
- 一个用户不能枚举、读取、更新或删除另一个用户的数据。

### 10.2 并发

- 同一用户的学习数据写入使用现有共享 `RLock`；
- 每次更新重新加载最新快照，避免旧内存快照覆盖新写入；
- LLM 和 RAG 只读生成调用不得持锁；
- 不同用户使用各自运行时和锁，可以并行。

### 10.3 删除

删除当前文档成功后，按 `document_id` 清理：

- plans；
- cards；
- exercises；
- 与上述卡片或题目关联的 review_logs。

清空全部文档成功后清空上述四个集合。现有笔记、报告和 Memory 语义保持不变。

RAG、History、源文件和 `learning.json` 不能组成单一跨资源事务。本 MVP 沿用现有文档删除顺序，在文档删除成功后于同一用户锁内提交学习数据级联清理。级联清理失败时必须：

- 返回部分失败；
- 保留可重试的错误状态；
- 不把操作报告为完整成功；
- 查询端过滤已不存在文档的孤立学习项，避免继续复习；
- 后续再次删除或清空时允许幂等重试清理。

## 11. 损坏处理

`LearningRepository` 在以下情况抛出 `CorruptLearningDataError`：

- 根值不是对象；
- `version` 不是整数 `1`；
- 四个顶层集合缺失或不是数组；
- 计划缺少 `id`、`document_id`、`document_name`、`title`、`target_date`、`daily_minutes`、`status` 或 `tasks`；
- 计划任务缺少 `id`、`due_date`、`phase`、`title`、`duration_minutes` 或 `completed`；
- 卡片缺少 `id`、`document_id`、`front`、`back`、`sources` 或 `schedule`；
- 练习缺少 `id`、`document_id`、`type`、`prompt`、`sources` 或 `schedule`；
- 复习日志缺少 `id`、`item_type`、`item_id`、`document_id`、`grade`、`result`、`reviewed_at` 或 `next_due_at`；
- 卡片或练习的调度结构缺少 `repetitions`、`interval_days`、`ease_factor` 或 `due_at`；
- 任何决定用户文档隔离、题型或调度的枚举值和字段类型不合法。

检测到损坏时：

- 读取和写入均失败关闭；
- 不自动写入空结构；
- 不覆盖原文件；
- UI 提示学习数据需要恢复；
- 不影响现有 History、Memory、RAG 和报告文件。

新增备份恢复 UI 不在本 MVP 范围内。

## 12. 测试与验收

### 12.1 Repository

- 缺少文件时返回版本化空结构；
- 保存使用原子替换；
- 更新基于最新磁盘快照；
- 非法根结构和非法记录失败关闭且原文件不变；
- 文档级联删除精确清理关联记录；
- 幂等重复删除不影响其他文档。

### 12.2 计划

- 拒绝空名称、非法日期、超出 1–365 天和非法分钟数；
- 1、2、3 天短计划使用明确阶段规则；
- 4 天及以上四阶段均非空且总任务数等于天数；
- 任务完成和恢复正确更新状态与完成率；
- 过期未完成任务仍可查询。

### 12.3 SM-2

- 评分 `1` 重置并安排 1 天后；
- 第一次成功安排 1 天后；
- 第二次成功安排 6 天后；
- 后续间隔按新 ease factor 计算；
- ease factor 不低于 `1.3`；
- 状态更新和 review log 同时提交；
- 到期查询边界包含 `due_at == now`。

### 12.4 生成

- 只检索当前 `document_id`；
- 数量 `3 / 5 / 10` 和混合题型分配正确；
- 非法 JSON 只进行一次结构修复；
- 伪造来源、错误数量、重复问题、非法选项和空字段整批拒绝；
- 来源只使用真实检索元数据；
- 生成期间文档被删除时不写入；
- 空检索、LLM 未配置和持久化失败不改变原数据。

### 12.5 Assistant 与 UI

- 未登录操作被拒绝；
- 当前选择不是恰好一个文档时拒绝；
- 卡片必须先展示答案再评分；
- 简答题必须先展示参考答案再评分；
- 选择题自动判分并更新调度；
- 删除单文档和清空全部文档联动学习数据；
- UI 返回稳定的下拉框、表格和状态更新。

### 12.6 多用户

- 两个用户可以拥有相同 `document_id` 而不串数据；
- 用户 A 不能看到、评分或删除用户 B 的计划、卡片、题目和日志；
- 同一用户并发生成和评分不会丢失更新；
- 不同用户的慢速生成互不阻塞。

自动化测试使用假 RAG、假 LLM 和可控时钟，不依赖网络、真实模型、Qdrant 或 Neo4j。交付前运行新增聚焦测试和现有完整 `pytest` 回归。

## 13. 成功标准

满足以下条件即视为 MVP 完成：

1. 登录用户能围绕当前单一文档创建学习计划并完成每日任务；
2. 用户能生成 3、5 或 10 张带真实来源的卡片；
3. 用户能生成混合、选择题或简答题批次并完成对应判分流程；
4. 卡片和题目每次评分后产生日志并得到正确的 SM-2 下次到期时间；
5. 学习数据在重启后恢复，且不同用户、不同文档完全隔离；
6. 删除文档不会留下可见或可复习的关联学习项；
7. 所有新增聚焦测试和现有回归测试通过。
