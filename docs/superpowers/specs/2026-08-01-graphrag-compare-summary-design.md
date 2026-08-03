# GraphRAG 对比与摘要融合设计

## 目标

将上一阶段的文档内图谱上下文扩展到 `compare` 和 `summary` 两种问答模式，
让跨文档对比和联合摘要能够引用 `G-*` 实体/关系证据，同时保留现有向量
检索、MMR、摘要缓存、异步进度和结构化对比行为。

## 范围

- `compare` 对每个选中文档查询图上下文，追加到受 token 预算约束的对比资料。
- 结构化对比允许经过校验的 `S-*` 和 `G-*` 引用。
- `summary` 在 map 阶段为每篇文档追加自己的图上下文，reduce 阶段只接收
  单篇摘要和对应的允许引用 ID。
- 摘要缓存键包含图上下文指纹和图模式，图谱变化不会复用旧摘要。
- `graph_mode=off|auto|required` 在普通、对比和摘要模式中保持一致。
- 最终答案和结构化 action data 同时展示向量来源和图谱来源。

## 非目标

- 不合并不同文档中的同名实体，不创建跨文档 Neo4j 边。
- 不改变图谱抽取、Neo4j schema 或写入生命周期。
- 不实现图谱可视化 UI。
- 不改造现有 MMR、异步摘要任务或结构化对比 schema。

## 数据流

`RAGTool._ask` 将图模式和限制传递给模式处理器。对比处理器先构造受保护的
逐文档向量结果，再查询选中文档图谱并把图上下文追加到 prompt。摘要处理器
在启动 map LLM 前一次性查询选中文档图谱；`required` 任一文档失败时不调用
任何 LLM，`auto` 则只使用成功的图上下文。

单篇摘要缓存键加入规范化图上下文的 SHA-256 指纹。缓存值保留该文档的
`graph_sources`，reduce prompt 的允许引用列表为向量和图谱引用的并集。

## 约束与失败行为

- 所有图查询仍由既有服务按 `(rag_namespace, document_id)` 隔离。
- `off` 不调用图服务。
- `auto` 图失败不影响原有对比或摘要结果。
- `required` 图不可用时必须在 LLM 调用前失败。
- 图上下文必须经过现有 token 预算限制，不得挤掉对比模式的每文档基础
  Chunk，也不得删除摘要模式的文档覆盖。

## 验证

- 对比：auto/off/required、选中文档隔离、`G-*` prompt/output、结构化引用。
- 摘要：map 注入、reduce 引用、缓存命中与图变化失效、required 零 LLM。
- 真实 Neo4j store live test保持通过。
- 项目 `venv` 聚焦测试、全量回归、compileall 和 diff check 全部通过。
