# 文献检索 Research Workspace 设计

日期：2026-09-12。状态：用户已确认设计方向及书面规格，并明确要求执行。

## 目标和范围

将现有 React 文献检索页升级为安静、克制、可追溯的证据工作台，与学习笔记工作台保持一致。保留全局 Sidebar，主要修改 React 组件及页面样式。继续使用现有 FastAPI 接口、用户隔离和来源校验。

采用完整工作台重构：比仅换肤更能建立研究范围与证据层级；不扩展后端，避免将视觉重构扩大成检索能力开发。用户已在对话中确认此方案。

## 布局与视觉

- 主内容居中，最大宽度 1560px；桌面为约 300px 研究范围与自适应证据区域。
- Header：Eyebrow「从资料到理解」，标题「文献检索」，说明「从自己的资料中寻找可信、可追溯的证据。」管理文档库为轻量链接，旧版入口收入更多操作。
- 背景 #F7F9F7，白色 Surface，主文字 #18352D，次文字 #68756F，主色 #176B55，边框 rgba(24,53,45,.10)。低对比文字仅用于非必要装饰，功能文本满足可读性。
- 标题 32–36px，区块标题 16–18px，正文 14–15px，Meta 12–13px，结果编号 12px 等宽字体。
- 主面板圆角 14px，输入区域 12px，按钮 10px，Chip 胶囊形；以边框、留白建立层级。
- 窄屏研究范围可折叠，单列证据；来源弹窗限制视口高度并可滚动。保留键盘操作、焦点返回与 reduced-motion 支持。

## 研究范围

标题「研究范围」与已选数 / 10；说明「仅在选中的资料中检索」「不会访问互联网」。搜索 placeholder 为「搜索资料...」。整行选择的资料项展示格式图标、名称和实际 file_suffix，可选展示实际文件大小。当前文档 API 没有页数和标签，不生成示例页数或概念标签。

隐藏视觉上的传统 checkbox，但保留 checkbox 语义、键盘操作和可见焦点。选中使用浅绿背景和细边框，右侧小勾。最多 10 份，已选项始终可取消。底部显示真实已选数量和清除选择。分别处理加载、错误、空文档库、筛选无匹配和已选文档被删除。

## 查询与检索状态

Research Command Bar 标题「你想从这些资料中找到什么？」；placeholder「描述你正在寻找的观点、概念、证据或问题……」。工具栏展示当前文档检索标识、5/10/20 条结果选择和「检索证据」按钮。查询最大 1000 字符，空查询、无有效范围或文档库不可确认时禁止提交。

POST /api/v1/search 请求仅有 query、document_ids、limit，不添加 mode。不提供可点击的 Hybrid / GraphRAG 选项。未来模式通过组件边界扩展，不增加无效交互。

初始状态展示低于 10% 不透明度的文档片段与引用连线装饰，以及「找到的不只是答案，而是可以回到原文的证据。」说明与四条用户指定的查询建议。点击建议只填写查询，不自动检索，也不暗示能生成总结。

检索中隐藏初始空状态，仅显示实际正在检索和提交范围数量；完成后显示真实返回数量。无实时阶段数据，不模拟理解、召回、重排进度或数字。零结果、错误与条件变化后的失效状态分别显示。保留现有请求取消和 generation 校验，任何过期结果不能打开来源或保存笔记。

## 证据和长期积累

EvidenceCard 展示 S-001 格式的本次结果编号、真实文档名、真实页码或缺失说明、摘录和相关度。编号仅用于本次列表阅读，不声称是后端持久 citation ID，也不替代 locator。相关度保留数值含义，不转成可信百分比。查询高亮通过 React 文本节点渲染，禁止注入 HTML。

主要操作「查看来源」「加入笔记」。前者打开现有来源摘录详情，明确不是全文；后者进入同一来源详情的可编辑笔记状态。更多菜单包含实际可用的文档问答入口和复制引用。问答仅携带文档范围，不声称已将某个片段注入问答上下文。

保存继续调用现有 createNote，传完整 source.kind=document_chunk 和 locator，保持 client_request_id 重试幂等、来源变化错误提示、未保存草稿保护、保存后打开笔记和查询失效处理。保留 document_id、chunk_id、chunk_index、content_sha256 等全部来源字段。

Knowledge Context 使用轻量空状态说明「暂无关联知识」，不生成概念树或 Memory 关联。知识片段保存、概念关联仅保留代码层扩展边界，不显示假功能按钮。

## 组件和数据边界

SearchPage 负责认证会话边界；SearchWorkspace 负责 selectedDocuments、query、documentFilter、resultLimit、selectedEvidence、详情初始编辑模式等 UI 状态。继续复用 features/search/api.ts、queries.ts 与 features/notes/api.ts，组件不拼接 fetch URL。

在 components/ResearchWorkspace 下按责任拆分：ResearchPageHeader、ResearchScopePanel（含 DocumentScopeItem）、ResearchCommandBar、RetrievalProgress、EmptyResearchState、EvidenceList（含 EvidenceCard 和 CitationBadge）、KnowledgeContext、EvidenceDetail。纯展示函数集中在 presentation.ts；样式继续由 styles/search.css 管理。避免为不存在的 API 创建空 API 文件。

请求结果保留原始 SearchResponse。UI 模式、筛选和打开状态不写回响应。保留按会话 remount、私人草稿不持久化和跨用户缓存隔离。

## 验收

1. 前端 lint、TypeScript 构建与单元测试通过；现有 search API、会话隔离和过期请求测试继续通过。
2. 更新页面测试，验证整行资料选择、10 份上限、建议填充、不同空状态、结果显示和来源保存入口。
3. 浏览器走通选择资料 → 检索 → 来源详情 → 编辑并保存笔记 → 打开笔记；保留来源失效及草稿保护验收。
4. 检查 1920×1080、1440×900、1280×800 和手机宽度下无横向溢出，内容宽度受限，折叠范围和弹窗可用；关键状态保存并查看截图。
5. 审查改动仅涉及本功能，明确报告提交、推送、测试及部署状态。本次不执行生产发布。

## 规格自检

已核对 SearchInput、SearchResponse、Document DTO 和现有来源保存流程。模式、分阶段进度、总页数、标签、全文查看与 Memory 关联均按当前接口能力约束；无虚构数据、待定字段或后端契约变更。
