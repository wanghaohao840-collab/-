# 多文档问答质量增强设计

## 目标

在现有多文档范围隔离、联合问答、对比分析和联合总结能力之上，按以下顺序提升质量与可运营性：

1. 建立可重复的 golden 评测基线；
2. 增加混合检索与 MMR 多样性排序；
3. 缓存单篇总结并按文档版本失效；
4. 为联合总结增加异步任务、进度和取消；
5. 改进来源交互与结构化对比输出。

本轮先实现第 1 阶段，后续阶段以评测结果作为回归门槛。

## 约束

- 保持 `UI → Assistant → Tool → RAG/Storage` 依赖方向；
- 不破坏 `document_id` 隔离、旧 `document_id` 调用和现有缓存格式；
- 运行和测试固定使用 `venv\Scripts\python.exe`；
- 评测基线不调用真实 LLM、不上传用户文档、不修改线上问答逻辑；
- 新增代码使用标准库，测试复用当前 pytest 环境。

## 第 1 阶段设计：golden 评测

新增 `evals/multi_document_qa.py`，提供从 JSON 加载案例、检查运行轨迹和汇总结果的纯函数。案例固定：问题、模式、所选文档、必须出现的文档、禁止出现的文档、最低 LLM 调用次数和必须出现的答案结构。评测同时检查全部 LLM prompt，而不是只检查最终 reduce prompt，以避免未选中文档在 map 阶段泄漏。

golden 数据放在 `evals/data/multi_document_qa.json`，包含联合问答、对比分析、联合总结和缺失信息四类最小案例。测试使用假的 Pipeline 和 LLM，验证真实 `RAGTool.execute("ask", ...)` 调用链；不要求 API Key 或外部服务。

## 后续阶段接口预留

- 混合检索：Pipeline 增加可选 `retrieval_mode`，默认保持 `vector`；候选合并后使用确定性的 MMR。
- 总结缓存：以 `(document_id, document_version, prompt_version)` 为键，文档替换/删除时失效。
- 异步总结：Assistant 提供任务 ID，后台最多 3 路 map，UI 轮询任务状态；取消只影响当前任务。
- 来源交互：答案来源携带稳定 citation ID、文档 ID、页码和原文片段；UI 提供可复制/定位入口。
- 结构化对比：保留 Markdown 降级，同时允许模型返回受校验的 JSON 结构。

## 验收

- golden 评测覆盖所有已支持模式和文档隔离；
- 评测命令在项目 `venv` 中通过；
- 评测不改变现有专项回归结果；
- 评测失败时能明确指出案例、缺失文档、泄漏文档或结构缺失。
