# AGENTS.md — iSite2 Codex 开发约束

你正在开发 iSite2：一个基于公开证据的高价值楼宇/室分机会扫描产品。所有代码、schema、API、UI 和输出模板必须符合以下约束。

## 最高优先级规则

1. **全量扫描优先**：Top N 只能是展示层视图，不能截断底层候选池。
2. **证据优先于判断**：任何 `Value Class`、`Action Class`、`Recommended Solution` 都必须能回溯到证据、来源、日期、证据等级和推测链路。
3. **分场景建模**：机场、会展、体育场、酒店、mall、办公、医院、大学、交通枢纽等使用各自的主指标和 Proxy，不允许统一打分一把梭。
4. **高价值证明链与现网状态链分离**：一个物业高价值已验证，不等于已建室分已验证。
5. **推测必须留痕**：推测只能补字段，不能覆盖直接证据，不能伪装成事实。
6. **主表保持短硬**：通用规则、公式、定义写入 Method/Parameters/Legend/Model Detail，不要逐行重复。
7. **Review Queue 必须可执行**：不要写“待研究”，要写“补查机场年报 2024 旅客吞吐量 / 查询运营商室分公告 / 核验 Google Maps 坐标”等具体动作。
8. **公开数据合规**：不得绕过登录、验证码、付费墙或 robots.txt；不得采集无关个人信息。
9. **扫网效率优先复用**：批量扫网、证据补足、图片替换、去重入库等流程必须优先复用已有脚本、缓存和标准质量门；除非现有流程无法覆盖关键需求，不新增代码或新脚本。脚本分层与标准动作链见 `docs/14_sweep_script_reuse.md`。
10. **目录页优先扫网**：扩充候选池时优先用 Firecrawl 找国家级/场景级目录页、官方列表页、行业目录页作为入口；先 search 留存目录 manifest，不直接大规模 scrape。只有目录页通过已知 URL/已知物业/bbox/场景主指标预过滤后，才 scrape 少量高质量页面并抽取候选。
11. **自动化 Firecrawl 入口固定**：自动化任务不得裸跑 `firecrawl ...`。使用 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- ...`，该入口默认清理 Codex shell 继承的本机代理变量；只有明确需要代理时才设置 `FIRECRAWL_TRUST_ENV_PROXY=1`。

## 开发风格

- 优先实现可测试的纯函数：demand calculation、gate validation、status enum validation、output column validation。
- 所有 agent 输入输出必须是结构化对象，禁止下游靠自然语言猜字段。
- 所有新增字段要同步更新：Pydantic model、JSON schema、DB schema、OpenAPI、测试。
- 先写/更新测试，再实现复杂逻辑。
- 对外部搜索、地图、PPT、Excel生成先做 interface 和 fake adapter，再接真实 provider。

## 关键数据对象

- `ScanRun`: 一次扫描任务。
- `ScanScope`: 国家/区域/城市边界与输出要求。
- `PropertyCandidate`: 候选楼宇/场景实体。
- `EvidenceItem`: 字段级证据。
- `SceneModelResult`: 分场景建模结果。
- `BuildStatus`: 现网室分状态。
- `DemandEstimate`: 需求链路计算结果。
- `InferenceRecord`: 推测留痕。
- `Conclusion`: Evidence Status / Value Class / Action Class / Recommended Solution。
- `ReviewItem`: 复核队列。

## 建议开发顺序

1. 数据模型、枚举、配置加载。
2. 需求链路和阶段门校验纯函数。
3. Orchestrator pipeline stub：Scope -> Discovery -> Entity -> Evidence -> Scene -> Build -> Demand -> Inference -> Conclusion -> QA -> Output。
4. FastAPI endpoint：创建任务、查询地图点、查询候选详情、导出任务。
5. DB migration 与 repository 层。
6. Excel skeleton 输出。
7. 世界地图 UI。
8. 真实搜索/地图/来源连接器。
