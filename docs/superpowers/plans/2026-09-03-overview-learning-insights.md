# 概览与学习洞察实现计划

1. 在现有 QA、笔记与报告领域接口上增加有界只读统计能力，并实现 `InsightsService` 聚合 DTO；不引入跨领域 SQL。
2. 增加 `/api/v1/overview` 与 `/api/v1/insights/*` 路由、Pydantic 模型、CSRF 生成和安全下载。
3. 增加 React 查询层、`OverviewPage`、`InsightsPage` 与响应式样式，替换两个迁移占位路由。
4. 使用隔离数据根测试多用户隔离、统计/报告 API 和前端交互；执行 Python/前端回归、生产构建、容器健康与深度冒烟。

非目标：学习时长、掌握度、连续打卡、后台报告队列、删除报告、GraphRAG、Neo4j 与分布式分析。聚合 DTO 保持可由未来事件流或物化视图实现。
