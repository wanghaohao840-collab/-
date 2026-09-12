# 文献检索工作台验收

## 交付范围

SearchPage 拆分为研究范围、查询工具栏、检索状态、证据列表、来源详情等组件；与学习笔记一致的浅色知识工作台样式。页面最大宽度 1560px，手机可折叠研究范围。查询建议只填写查询。证据卡片直接进入来源笔记编辑，更多菜单提供文档问答和复制引用。

FastAPI、检索 API、DTO、用户隔离、locator 与笔记 source 请求契约均未修改。S-001 是本次列表编号。页面不伪造全文入口、模式切换、召回阶段、可信百分比或 Memory 数据。

## 验证证据

- `npm test -- --reporter=dot`：27 个测试文件、203 项测试通过。覆盖范围与查询约束、建议不自动请求、直接来源笔记入口、来源失效、幂等重试、会话变化、过期结果和安全高亮。
- `npm run lint`：通过。
- `npm run build`：通过；现有主 JS bundle 大于 500 kB 的提示仍存在，本次不扩展构建拆包范围。
- Playwright `e2e/search.spec.ts`：桌面、平板、手机真实服务链路通过。测试服务使用隔离数据和测试账号，不是生产账号验收。
- 最后宽屏调整后，平板和手机通过；桌面曾在删除后轮询中直接读取非成功响应的 sources 导致测试脚本异常。轮询改为显式检查 status=200 与 deleted=true，桌面重跑通过（21.5s）。
- 验收链路：注册 → 导入 → 选择范围 → 检索 → 复制来源 → 直接编辑并保存来源笔记 → 打开笔记 → 文档问答范围确认 → 删除原文并确认笔记正文保留、来源摘录清除。
- 检查 1920×1080、1440×900、1280×800、1024×768、390×844；包含无严重/关键 axe 违规和无横向溢出断言。已查看宽屏、1280 空状态、平板和手机结果截图。

## 本地证据位置

- `.runtime/research-unit.log`
- `.runtime/research-lint-final.log`
- `.runtime/research-build-final.log`
- `.runtime/research-e2e-final.log`（第一次三端全部通过）
- `.runtime/research-verified.log`（最终布局下平板/手机通过及桌面轮询失败记录）
- `.runtime/research-desktop-final.log`（桌面最终通过）
- `.runtime/research-desktop-final/`（最终桌面截图）
- `.runtime/research-verified/`（平板与手机截图）

## 发布边界

本次仅实现、构建和验证前端，未部署新版容器。与本功能无关的现有部署改动不包含在提交中。
