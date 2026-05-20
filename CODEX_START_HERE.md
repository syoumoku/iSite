# CODEX_START_HERE

把本包放进 repo 根目录后，第一条发给 Codex 的指令建议如下：

```text
请先读取 AGENTS.md、.agents/skills/isite2-scan/SKILL.md、docs/02_architecture.md、config/*.yaml、schemas/*.schema.json 和 tasks/codex/00_bootstrap_foundation.md。

目标：把 iSite2 starter pack 做成可运行的 Phase 0/1 MVP。先不要接真实爬虫，先实现可测试的后端规则引擎、pipeline stub、FastAPI endpoints、DB repository 接口和 Excel skeleton 输出。

硬约束：
1. full_scan 不得截断候选池，Top N 仅是展示层。
2. 证据先于判断，结论必须能追溯证据。
3. 分场景建模，不做默认统一总分。
4. 现网建设状态独立判断，不能由楼宇价值推断。
5. 推测必须写推测留痕，不能覆盖直接证据。
6. Review Queue 必须写具体下一步动作。
7. 主表固定表头，Google地图链接必须保留。

验收：pytest -q 通过；ruff check src tests 通过；FastAPI /health、/rules/scenes、/rules/output-template、/scan-runs 可用；能生成符合 config/output_templates.yaml 的 Excel skeleton。
```

## 推荐拆分给 Codex 的后续任务

1. `tasks/codex/00_bootstrap_foundation.md`
2. `tasks/codex/01_database_and_repository.md`
3. `tasks/codex/02_orchestrator_pipeline.md`
4. `tasks/codex/03_excel_output.md`
5. `tasks/codex/04_map_api.md`
6. `tasks/codex/05_ui_world_map.md`
7. `tasks/codex/06_connectors_and_source_cache.md`
8. `tasks/codex/07_ppt_cards.md`
9. `tasks/codex/08_growth_loop.md`
