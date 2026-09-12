# Notes Workspace Implementation Plan

**Goal:** 将笔记三栏升级为安静的知识工作台，保持现有 API、保存与隔离规则。

**Architecture:** 页面保留查询和导航，工作台保留草稿/版本状态；展示组件拆分 Header、Filters、Metadata、Tabs 和 SourceCard。标题通过 Markdown 一级标题存储。

**Tech Stack:** React、TypeScript、现有 React Query API 层、CSS、Vitest、Playwright。

## Global Constraints

不修改 FastAPI；不伪造来源操作、AI 能力或 Memory 推荐。现有未保存保护和版本冲突必须保留。仅改 web 笔记相关代码和本任务文档。

## Tasks

- [x] 添加 notePresentation 标题读写、日期和来源显示工具；验证无标题旧文档不会自动改变、显式标题编辑保留正文。
- [x] 拆分 NotesPageHeader、NotesFilters、NoteMetadataForm、NoteEditorTabs；重构 NoteList 与 NoteEditor，保留现有保存回调及测试定位语义。
- [x] 来源卡片显示真实 citation/locator，复用已有 QA 路由；没有 document viewer 时只呈现定位，不提供虚假跳转。添加上下文空态和已知概念。
- [x] 将筛选放入左栏；增加独立清空筛选动作；清空笔记移入更多操作；手机筛选和来源继续使用现有焦点管理。
- [x] 更新 notes-workspace.css，保持桌面三栏、平板来源抽屉、手机列表/编辑切换。
- [x] 运行 `npm.cmd test`、`npm.cmd run lint`、`npm.cmd run build`。针对 Notes 三档运行 Playwright，审阅并更新必要快照，再无更新复验。记录结果并只提交本轮文件。

## 验证结果

2026-09-12：201 项单元测试通过，lint、TypeScript/生产 build 通过。Notes 三档来源保存/删除提示/投影重试通过；最终生命周期及无更新视觉复核 6 passed，涵盖版本冲突、筛选、分页、清空、无障碍、溢出和手机焦点顺序。初轮的旧“上次保存”断言已修正并重跑。标题和副标题的对比度问题已修复。未修改 FastAPI 或部署生产。

构建仍有约 551kB JS chunk 提示。无新依赖。桌面与手机预览见 web/e2e/notes.spec.ts-snapshots。
