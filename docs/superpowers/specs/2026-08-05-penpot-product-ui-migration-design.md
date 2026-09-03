# 知研 Penpot 产品 UI 迁移设计

## 1. 目标

将“知研 · 智能文档学习助手”的产品 UI 设计源从 Figma 完整切换到 Penpot。Penpot 云端文件成为设计 Token、组件、页面、响应式规则和交付信息的唯一设计来源；现有 Figma 文件只保留为只读归档，不再参与同步、实现或验收。

本迁移不改变已完成的共享应用服务、CSRF 会话契约和 FastAPI 认证 API，也不改变现有用户 UUID 数据目录、`document_id` 隔离、来源页码、引用、RAG、Memory、报告、批量导入任务和存储契约。

## 2. 已批准决策

- 使用 Penpot 云端和官方 Remote MCP。
- 在 Penpot 原生重建设计，不导入 Figma 文件，也不从 React 反建设计。
- Penpot 是唯一设计源，Figma 仅归档。
- 保留“知研”品牌名和“智能文档学习助手”副标题。
- 保留简洁、专业、翡翠绿色的学术工具视觉方向。
- 完整支持桌面、平板和手机三档视口。
- 使用 DTCG Token、Penpot 组件映射和浏览器截图验收替代 Figma Variables、Code Connect 和 `.figma.tsx`。

## 3. 外部连接与安全边界

Penpot Remote MCP 通过用户在 Penpot 云端生成的服务器 URL 连接。该 URL 包含个人 MCP 密钥，必须直接配置在 Codex 的 MCP 设置中，不得：

- 发送到对话；
- 写入仓库、文档或测试快照；
- 出现在日志、截图或提交历史中。

执行写操作前必须完成只读检查，确认：

1. MCP 工具可用；
2. 当前活动文件名称正确；
3. 当前活动 Penpot 标签页已连接 MCP；
4. 当前页面与即将写入的阶段一致。

Penpot MCP 只操作当前活动页面。活动标签页变化、连接中断或读取结果不完整时，立即停止写入，保留恢复记录，不使用浏览器脚本或猜测值代替真实文件、页面或组件 ID。

## 4. 设计文件结构

Penpot 云端文件命名为“知研 · 智能文档学习助手”，包含七个页面：

1. `00 Foundations`：颜色、字体、间距、圆角、阴影、断点、网格和无障碍说明。
2. `01 Components`：公共组件、Variants、状态和用法。
3. `02 Desktop`：桌面登录、注册、应用壳和会话过期页面。
4. `03 Tablet`：平板登录、应用壳、紧凑导航和抽屉。
5. `04 Mobile`：手机登录、应用壳、底部导航和“更多”抽屉。
6. `05 States`：加载、验证错误、API 错误、未认证、会话过期和功能迁移空状态。
7. `06 Handoff`：组件说明、响应式规则、键盘与焦点规则、代码映射和验收状态。

页面、组件和关键画板 ID 必须由 Penpot MCP 实际返回并记录，禁止手写或猜测。

## 5. Design Token 契约

### 5.1 颜色

| Token | Value |
|---|---|
| `color.brand.100` | `#E6F3ED` |
| `color.brand.600` | `#287A60` |
| `color.brand.700` | `#1F634D` |
| `color.canvas` | `#F5F8F6` |
| `color.surface` | `#FFFFFF` |
| `color.text.primary` | `#263B34` |
| `color.text.secondary` | `#71847C` |
| `color.border` | `#DCE7E1` |
| `color.danger` | `#C43D4B` |
| `color.warning` | `#A86816` |
| `color.success` | `#287A60` |
| `color.focus` | `#2F80ED` |

### 5.2 尺寸

- 间距：`space.0=0`、`space.1=4`、`space.2=8`、`space.3=12`、`space.4=16`、`space.5=20`、`space.6=24`、`space.8=32`、`space.10=40`、`space.12=48`。
- 圆角：`radius.sm=6`、`radius.md=10`、`radius.lg=16`、`radius.pill=999`。
- 布局：`sidebar.width=248`、`topbar.height=64`、`mobile-nav.height=64`、`content.max-width=1200`。

### 5.3 字体和效果

- 拉丁字母和数字首选 Inter。
- 中文使用平台 CJK 无衬线字体回退；若 Penpot 云端无法使用目标字体，在 Handoff 中记录实际替代和浏览器差异。
- 字体样式：`Display/32/40/Semibold`、`Heading/24/32/Semibold`、`Title/18/26/Semibold`、`Body/16/24/Regular`、`Body/14/22/Regular`、`Label/14/20/Medium`、`Caption/12/18/Regular`。
- 阴影样式：`Shadow/Surface` 和 `Shadow/Overlay`。

Token 在 Penpot 中维护并以 W3C DTCG JSON 发布到 `design/tokens/zhiyan.tokens.json`。仓库脚本从该文件生成 `web/src/styles/tokens.css`。设计值只允许通过“Penpot → DTCG JSON → CSS”链路发布，禁止直接在页面组件中散落硬编码，也禁止在代码侧反向覆盖 Penpot Token。

## 6. 公共组件

Penpot 组件库包含：

- 基础：Button、IconButton、TextField、PasswordField、Checkbox、Badge、Avatar、Tooltip；
- 反馈：Toast、Dialog、Drawer、EmptyState、Skeleton；
- 导航：Tabs、SidebarItem、Sidebar、MobileBottomNav、TopBar、PageHeader；
- 布局：AppShell。

组件必须使用 Penpot Flex/Grid Layout、语义 Token 和组件实例。页面不得使用脱离主组件的复制品。

关键 Variants：

- Button：`hierarchy=primary|secondary|ghost|danger`、`size=sm|md|lg`、`state=default|hover|focus|disabled|loading`、`icon=none|leading|trailing`；
- TextField：`state=default|hover|focus|filled|error|disabled`、`label=on|off`、`helper=none|help|error`；
- SidebarItem：`state=default|hover|active|focus`、`collapsed=true|false`；
- Toast：`tone=info|success|warning|error`；
- Dialog：`size=sm|md|lg`；
- AppShell：`viewport=desktop|tablet|mobile`。

焦点状态使用可见的 2px 焦点环。禁用和破坏性状态不能只依靠颜色区分。图标统一使用同一套轮廓风格和 20/24px 尺寸。

## 7. 页面与响应式规则

### 7.1 桌面

- 固定视口：`1440×1024`；
- 固定 248px 侧栏和 64px 顶栏；
- 内容最大宽度 1200px；
- 侧栏项目：概览、文档库、智能问答、文献检索、学习笔记、学习洞察。

### 7.2 平板

- 固定视口：`1024×768`；
- 72px 紧凑导航轨道和按需抽屉；
- 仅当每列至少保留 320px 时使用双列布局。

### 7.3 手机

- 固定视口：`390×844`；
- 单列内容；
- 底部导航：概览、文档、问答、检索、更多；
- “更多”抽屉：学习笔记、学习洞察、账户、退出登录；
- 交互目标最小尺寸 44px。

三个视口均提供登录、注册和 AppShell。登录与注册包含用户名、密码、行内验证、等待状态和服务器错误提示。尚未迁移的功能使用统一空状态：“该能力正在迁移到新版界面，可暂时前往旧版使用。”，并提供指向 `/legacy` 的次级操作。

## 8. 无障碍要求

- 正文和控件达到 WCAG AA 对比度；
- 所有交互元素具有可见焦点状态；
- 文档中明确键盘顺序、抽屉焦点管理和退出方式；
- 页面使用唯一、清晰的标题层级；
- 不能只依赖颜色传达错误、禁用、警告或选中状态；
- React 实现使用语义化 `nav`、`main`、标题和按钮，并提供跳转到主内容的链接；
- Playwright + axe 在三档视口对登录页和已认证应用壳执行检查，严重及致命问题必须为零。

## 9. Penpot 到代码交付链路

### 9.1 Token 发布

`design/tokens/zhiyan.tokens.json` 是 Penpot 发布到仓库的 DTCG 快照。生成脚本负责创建 `web/src/styles/tokens.css`，CI 必须验证生成结果与提交内容一致。Token 同步失败、格式无效或生成结果不一致时阻止构建。

### 9.2 组件映射

创建 `docs/product-ui/penpot-component-map.json`，每条记录包含：

- Penpot 文件和组件真实 ID；
- Penpot 组件名称；
- React 组件文件；
- Props 与 Penpot Variants 对照；
- 最近验证状态。

该映射替代 Figma Code Connect。仓库不创建 `.figma.tsx` 文件，也不运行 Code Connect CLI。

### 9.3 Handoff

创建 `docs/product-ui/penpot-handoff.md`，记录：

- Penpot 文件真实链接；
- 七个页面和关键组件真实 ID；
- 桌面、平板、手机关键画板 ID；
- 响应式、交互、焦点和字体回退规则；
- Token 发布和组件映射状态；
- 视觉验收状态和已知浏览器差异。

### 9.4 视觉验收

从 Penpot 导出三个目标视口的参考图。Playwright 在相同视口生成 React 截图，人工对照布局、字体、颜色、状态和导航行为。只有完成对照并解释浏览器渲染差异后，才建立或更新稳定截图基线。

## 10. 执行阶段

1. **连接准备**：启用 Remote MCP，在 Codex 中安全配置服务器 URL，新建并打开目标 Penpot 文件，连接当前标签页。
2. **计划迁移**：修订现有 Figma 设计和实施计划中的工具、文档、Code Connect 与验收描述；已完成的后端任务 2–4 保持不变。
3. **设计重建**：按 Foundations → Components → Desktop → Tablet → Mobile → States → Handoff 顺序执行；每一阶段完成结构和截图检查并记录恢复点。
4. **React 实施**：设计批准后实现认证、受保护路由和三档 AppShell。
5. **统一运行时**：将 React、FastAPI 和 Gradio `/legacy` 合并到单一进程、单一 Worker 和同一组 ApplicationServices。
6. **最终验收**：执行 Python、React 单元测试、类型检查、生产构建、Playwright、axe、视觉回归、Token 同步检查和统一服务器烟雾测试。

## 11. 故障和恢复策略

- MCP 未连接、活动标签页改变或页面身份不符：停止写入，重新只读确认。
- Penpot 操作只完成部分节点：记录已返回 ID，检查实际文件状态后从最小未完成单元恢复，不重复创建已存在组件。
- Token 导出或 CSS 生成不一致：阻止构建并修复单一来源，不手工修改生成结果掩盖差异。
- 字体不可用：使用批准的 CJK 系统字体回退并记录差异，不静默替换。
- 页面或组件 ID 无法打开：Handoff 不得标记完成。
- 视觉截图存在溢出、裁切、错位或未解释差异：不得建立基线或进入下一阶段。
- Figma 归档与 Penpot 不一致：以批准后的 Penpot 设计为准，不进行双向同步。

## 12. 验收标准

1. Penpot 文件包含七个批准页面，文件、页面和关键组件 ID 均真实可访问。
2. Token、组件和页面使用统一的语义契约，没有散落的品牌色、间距和圆角硬编码。
3. 公共组件具备批准的 Variants、交互状态和无障碍说明。
4. 桌面、平板、手机页面无裁切、无溢出，导航和抽屉行为明确。
5. DTCG Token 可导出并稳定生成 React CSS Token；CI 可检测漂移。
6. Penpot 组件与 React 组件通过映射清单建立真实、可验证的对应关系。
7. 三档 Playwright、axe 和视觉对照通过。
8. Figma 专属 Code Connect 和 `.figma.tsx` 不再出现在实施路径中。
9. 已完成的后端架构和数据隔离契约保持不变。
10. MCP 密钥未进入仓库、日志、截图或对话。

## 13. 参考资料

- Penpot MCP：<https://help.penpot.app/mcp/>
- Penpot Design Tokens：<https://help.penpot.app/user-guide/design-systems/design-tokens/>
- Penpot Components：<https://help.penpot.app/user-guide/design-systems/components/>
- Penpot Variants：<https://help.penpot.app/user-guide/design-systems/variants/>
- Penpot Dev Tools：<https://help.penpot.app/user-guide/dev-tools/>
- Penpot 文件格式：<https://help.penpot.app/user-guide/export-import/penpot-file-format/>
