# Codex 工作流建议

## 首轮 Prompt

```text
请读取 AGENTS.md、.agents/skills/isite2-scan/SKILL.md、docs/02_architecture.md、config/*.yaml、schemas/*.json 和 tasks/codex/00_bootstrap_foundation.md。先不要接真实爬虫，先实现可测试的后端规则引擎、pipeline stub、FastAPI endpoints 和 Excel skeleton 输出。
```

## 子 agent 并行任务建议

当 Codex 支持 subagents 时，可以让它：

```text
请 spawn 5 个子 agent 并行评审当前实现：
1. 数据契约和 DB schema 一致性
2. 证据/推测/Review Queue 规则一致性
3. Demand 公式与测试覆盖
4. Output Excel 固定表头和 sheet 规则
5. API 与地图 GeoJSON 输出
等待全部结果后汇总修复计划。
```

## 每次 PR 必做检查

- `pytest -q`
- `ruff check src tests`
- JSON schema 与 Pydantic model 是否同步
- OpenAPI 与 endpoint 是否同步
- DB schema 与 repository 是否同步
- Review Queue 是否写具体动作
