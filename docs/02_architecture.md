# iSite2 架构设计

## 系统分层

```text
[World Map UI / Output Portal]
        |
[FastAPI Gateway]
        |
[Scan Orchestrator + Queue]
        |
+---------------- Multi-Agent Pipeline ----------------+
| Scope -> Discovery -> Entity -> Evidence -> Scene    |
| -> BuildStatus -> Demand -> Inference -> Conclusion  |
| -> QA -> Output                                      |
+-------------------------------------------------------+
        |
[Rules Engine + Config]
        |
[PostgreSQL/PostGIS + Object Storage + Source Cache]
```

## 后台滚动扫描

- `ScanRun` 表记录每轮扫描。
- 调度器按 `scan_scope` 生成子任务：国家 -> 城市 -> 场景 -> 候选点。
- 每个子任务都写入 `scan_events`，支持失败重试和批次追踪。
- 新证据进入后触发重新计算：Scene Model、Demand、Conclusion、QA。

## Agent 编排策略

MVP 阶段不需要马上把每个 agent 都做成独立 LLM worker。建议先实现：

1. 结构化 pipeline：每步有输入输出模型。
2. agent adapter：每步可以接 rule-based / LLM / human-review 三种执行器。
3. worker queue：长任务异步跑。
4. Review Queue：不确定项进入人工复核，而不是让模型硬编。

## 数据闭环

```text
Candidate -> Evidence -> Metric -> Inference -> Conclusion
     ^                                             |
     |                                             v
Human Review <- Review Queue <- QA <- Output Feedback
```

## 技术关键点

- DB 使用 PostGIS 存地图坐标。
- Evidence 以字段级多行保存，不把来源塞进主表长文本。
- Inference 独立表保存，不和 Evidence 混淆。
- Output Artifact 独立表，记录 Excel/PPT/地图导出批次。
- 所有规则配置化，避免硬编码在 prompt 里。
