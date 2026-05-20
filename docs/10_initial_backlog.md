# iSite2 初始产品 Backlog

## P0 必做

- 后端规则引擎：demand formula、stage gates、status enum validation。
- 数据契约：Pydantic + JSON Schema + DB schema + OpenAPI 同步。
- ScanRun 创建与状态查询。
- Fake Discovery / Fake Evidence，跑通一轮候选 -> 证据 -> 建模 -> 需求 -> 结论 -> QA。
- Excel skeleton 固定 sheet 与表头。
- GeoJSON map endpoint。

## P1 产品可用

- 真实搜索 provider interface。
- source cache 与 rate limiter。
- evidence tier classifier。
- Review Queue UI/API。
- 世界地图筛选。
- 单点 drawer 展示证据链、推测链、现网状态。

## P2 商业化交付

- PPT 单点洞察卡。
- 国家/城市/场景汇总。
- 筛选后批量导出。
- 用户自定义输出模板。
- 人工复核反馈闭环。

## P3 自动成长

- 规则版本管理。
- 推测证伪/证实样本库。
- 来源可信度评分。
- 自动重扫调度。
- 新国家/新场景接入向导。
