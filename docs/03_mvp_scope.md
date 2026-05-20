# MVP 范围

## Phase 0 — 工程基础

- Python backend scaffold
- Pydantic models
- JSON schemas
- Postgres schema
- OpenAPI draft
- Config loader
- Demand formula tests
- Gate validation tests

## Phase 1 — 单国家扫描闭环

- 创建 ScanRun
- 手工或 fake discovery 写入候选池
- 写入 EvidenceItem
- 运行 Scene Modeling / Demand / Conclusion / QA
- 导出 Excel skeleton
- 提供 GeoJSON map endpoint

## Phase 2 — 真实公开证据连接器

- Search provider interface
- Source cache
- Source tier classifier
- Evidence extraction job
- Cross-check job
- Robots/rate-limit middleware

## Phase 3 — UI

- 世界地图
- 国家/场景/证据状态/行动类别筛选
- 物业详情 drawer
- 证据链查看
- Review Queue 面板
- 输出任务按钮

## Phase 4 — 自动成长

- 人工复核反馈写回
- 错误推测样本库
- 场景规则版本管理
- Evidence freshness scheduler
- 国家扫描优先级推荐
