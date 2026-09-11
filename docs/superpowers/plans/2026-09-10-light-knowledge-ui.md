# 知研浅色知识感 UI Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. 用户已审阅设计并要求继续执行，本轮顺序实施，不新增代理。

**Goal:** 实现参考图左侧的浅色知识感登录页、外壳、首页、问答与文档库。

**Architecture:** 复用 React 路由、认证、查询 hooks 和现有 DTO。只在展示层增加真实活动曲线与视觉组件，不改变 FastAPI、数据隔离或存储。

**Tech Stack:** React 19、TypeScript、CSS、原生 SVG、Vitest、Playwright。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-09-09-light-knowledge-ui-design.md`，用户于 2026-09-10 审阅通过。
- 修改 `design/tokens/zhiyan.tokens.json` 后生成 `web/src/styles/tokens.css`；不要直接维护生成的 CSS。
- 首版完成标准是四个主要页面的视觉与使用体验统一，不包含图谱和记忆服务的新增实现。
- 不依赖远程字体；不展示虚假的记忆数量、理解程度或相关度。
- 保留 1440×1024、1024×768、390×844 验证档位，增加 320px 溢出检查。
- 当前实现基线：`5e311e3`。已有 Windows 部署与 GraphRAG 文档改动不属于本轮。
- 实施采用当前 `codex/batch-import-async-tasks` 工作区，逐文件检查差异，只提交本轮文件。

## Task 1 — 视觉基础与登录

**Files:** `design/tokens/zhiyan.tokens.json`、生成的 `web/src/styles/tokens.css`、`web/src/styles/global.css`、`web/src/styles/app-shell.css`、`web/src/components/AuthIntro/AuthIntro.tsx`、`web/src/components/Sidebar/Sidebar.tsx`；新增 `web/src/components/BrandMark/BrandMark.tsx`、`web/src/components/NavigationIcon/NavigationIcon.tsx` 和 `web/src/assets/knowledge-pages.png`。

**Interfaces:** 保留 `AuthIntro()`、`Sidebar({isLogoutPending,onLogout})`。新增 `BrandMark()` 无参数，`NavigationIcon({path}:{path:string})` 只负责装饰，不承担导航事件。

- [ ] 基线执行 `npm.cmd test -- --reporter=dot`（cwd `web`），记录已有失败。
- [ ] 通过 imagegen 制作白底半透明层叠书页与浅绿色山景装饰，通过 Vite import 引入，构建到 /assets；正文由 DOM 渲染。
- [ ] 设置 canvas `#F6F8F7`、surface `#FFFFFF`、primary `#20352D`、secondary `#61746B`、brand100 `#EAF3EF`、brand700 `#1F634D`、border `#E0E8E3`、sidebar216px、content1280px。
- [ ] 执行 `node scripts/design_tokens.mjs design/tokens/zhiyan.tokens.json web/src/styles/tokens.css`。
- [ ] 登录介绍使用“让每一次阅读／成为长期的知识资产”；去除“已同步”和 document_id 文案，保留登录注册表单行为。
- [ ] 桌面大标题 48px 宋体栈，正文 16px；书页在标题下方，右侧登录卡片居中；移动端只保留紧凑品牌介绍。
- [ ] 侧栏使用线性图标与立方体品牌标记；选中状态浅绿底与左侧墨绿短线，所有现有入口保留。
- [ ] 执行 token check 与认证、外壳相关单元测试，检查登录/注册三档截图。

## Task 2 — 真实学习首页

**Files:** `web/src/pages/OverviewPage.tsx`、`web/src/pages/OverviewPage.test.tsx`、`web/src/styles/insights.css`；新增 `web/src/components/ActivityTrend/ActivityTrend.tsx` 及测试。

**Interfaces:** `ActivityTrend({days}:{days:ActivityDay[]})`；数据来自 `useOverview()`，用户名来自 `useAuth()`。`ActivityDay` 已存在于 `web/src/features/insights/types.ts`。

- [ ] 为曲线写三个行为测试：空列表显示“还没有学习活动”、单日数据能读到日期与总次数、多日数据总次数准确且保留 UTC 字符串。
- [ ] 先运行该测试确认尚未实现，再实现原生 SVG 曲线；提供 details/table 查看数据，不需要额外图表依赖。
- [ ] 使用以下坐标逻辑，不将每日值变成累计掌握度：

```ts
const max = Math.max(1, ...days.map((day) => day.total));
const points = days.map((day, index) => ({
  x: days.length === 1 ? 160 : 12 + index * 296 / (days.length - 1),
  y: 108 - day.total / max * 92,
}));
```

- [ ] 首页顶部真实用户名问候、导入按钮；上层活动卡、知识资产卡、学习提示卡，下层最近文档与最近问题。
- [ ] 知识资产严格对应 `document_count`、`completed_question_count`、`note_count`、`report_count`；趋势标签与 `window_days` 一致。
- [ ] 最近问题不含会话 ID，入口仅为 `/qa`。文档入口为 `/qa?documents=${encodeURIComponent(document_id)}`。
- [ ] 更新测试 fixture 提供完整 LearningStats 和认证 mock；保留加载、失败重试、空状态测试。
- [ ] `npm.cmd test -- src/pages/OverviewPage.test.tsx src/components/ActivityTrend/ActivityTrend.test.tsx`；检查长用户名与无数据布局。

## Task 3 — 问答阅读与引用

**Files:** `web/src/styles/qa.css`、`web/src/pages/QaPage.tsx`、`web/src/components/QaWorkspace/QaWorkspace.tsx`、相关现有测试。

**Interfaces:** 保留全部 ConversationList、MessageList、QaComposer、SourcePanel、SummaryStatus 参数及事件。保留 API 使用的 conversation 命名。

- [ ] 可见线程标题改为“学习线程”，来源标题与抽屉名改为“引用证据”；保持现有创建/删除按钮行为和测试可访问入口。
- [ ] 桌面三栏使用 `200px minmax(0,1fr) 260px`；1440px 时中央区应至少约480px。
- [ ] 中间白底阅读区、16px/1.8 正文，用户消息浅绿，长引用自动断行。
- [ ] 本轮保留 1200px 折叠断点，与 `showSources` 中 `window.innerWidth < 1200` 一致；1199px 以下通过原有线程/引用抽屉访问，避免 CSS 与交互条件分离。
- [ ] 输入框保持布局流内，使用 thread 的 flex 底部对齐，不引入会遮挡正文的固定定位。
- [ ] 展示 source 的真实 citation_id；无页码时隐藏页码。摘要显示真实 stage/progress，不模拟分步推理。
- [ ] 运行 `npm.cmd test -- src/pages/QaPage.test.tsx src/components/QaWorkspace/QaWorkspace.test.tsx`。
- [ ] 浏览器检查1200px和1199px边界、长回答、引用复制、抽屉 Escape/焦点恢复。

## Task 4 — 文档库

**Files:** `web/src/components/DocumentToolbar/DocumentToolbar.tsx`、`web/src/components/DocumentList/DocumentList.tsx`、`web/src/styles/documents.css`。

**Interfaces:** Document DTO 仅有 document_id/name/file_suffix/size_bytes/loaded_at/status；保留 `onAsk`、`onDelete` 和全部导入控件。

- [ ] 副标题改为“每一次阅读，都有迹可循”，保留过滤框与导入按钮。
- [ ] 文件图标使用格式缩写；完整名称通过 title 可获取，元数据保留格式、大小、导入时间。
- [ ] 列表白底轻边框、行间留白；移动端纵向布局、操作目标至少44px，取消过小的固定页面宽度限制。
- [ ] 保留导入成功“已导入”与失败/取消/重试语义，不改为“已理解”。
- [ ] 执行 `npm.cmd test -- src/pages/DocumentsPage.test.tsx src/components/ImportBatchPanel/ImportBatchPanel.test.tsx src/components/ImportDialog/ImportDialog.test.tsx`。

## Task 5 — 验证与交付

**Files:** `web/e2e/visual.spec.ts`、受文案影响的 e2e 定位器和快照；`docs/product-ui/README.md`；本计划的执行记录。

- [ ] 更新产品 UI 说明：本轮参考图覆盖视觉方向，旧 Penpot 画板不宣称已同步。
- [ ] 根目录执行 `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css`。
- [ ] `web` 执行 `npm.cmd run typecheck`、`npm.cmd run lint`、`npm.cmd test -- --reporter=dot`、`npm.cmd run build`。
- [ ] 使用现有 e2e fixtures，确保后端解释器是项目 venv，隔离测试数据；执行 auth-shell、documents、qa 和 visual 用例（以磁盘实际测试文件名为准）。
- [ ] 检查截图再更新快照，重新运行不带 update 的快照检查。
- [ ] 检查登录、首页、问答、文档库三档与320px溢出；抽查笔记、检索、洞察、学习中心。
- [ ] 只提交本轮明确文件；记录测试结果、视觉截图、生成插画来源和未完成项。生产部署不属于本轮。

## 设计覆盖与自查

登录与注册、外壳对应任务1；首页真实数据对应任务2；引用与线程对应任务3；文档库对应任务4；响应式、可访问性、设计源说明及回归对应任务5。记忆与图谱按已批准设计保留后续范围。

## 执行记录

2026-09-10：计划完成；用户已要求继续执行。基线 196 测试通过。实现后初轮类型检查、lint、构建通过；新增活动曲线测试先验证缺少组件失败，再实现组件。浏览器检查发现 public/images 不在服务端静态挂载路径，已改为 Vite import 生成 /assets 资源；用户名前端 maxlength 为32，e2e 断言同步实际输入值。视觉与交互验证进行中。

2026-09-11：任务1–4实现完成，任务5的功能回归与视觉检查已完成；全档无更新快照复核因测试后端启动超时未完成。详见 docs/product-ui/light-knowledge-delivery.md，按真实结果交付，不将超时计为通过。
