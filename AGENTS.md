# AGENTS.md — iSite2 Codex 开发约束

## 交接与新环境入口

新负责人或新 agent 先阅读 `docs/16_handover.md` 和 `docs/19_handover_inventory.md`；
日常提示词见 `docs/17_codex_prompt_playbook.md`，部署与恢复见 `docs/18_deployment_runbook.md`。
`tasks/codex/` 与早期架构文档包含历史计划，不能当成尚未实现的当前任务重新执行。

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
11. **自动化 Firecrawl 入口固定**：自动化任务不得裸跑 `firecrawl ...`。使用 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- ...`；该入口默认连接本地 Docker Firecrawl `http://127.0.0.1:3002/v1`，不读取 API key、不消耗云端 credits，并清理 Codex shell 继承的本机代理变量。只有人工明确要求云端时才允许传 `--deployment cloud`；只有明确需要代理时才设置 `FIRECRAWL_TRUST_ENV_PROXY=1`。
12. **国家本地化搜索前置**：每个国家扫网或补证前，必须先识别并留存该国主要官方语言、实际优先搜索语言、主要使用的搜索引擎/搜索渠道、本地域名和场景主指标本地化术语；先用官方语言和本地搜索渠道寻找本地官方/运营方/行业来源证据，英文或国际目录只能作为补充、交叉验证或本地证据失败后的兜底。
13. **QA 控制前置**：每次 QA 检查、扫网收尾、输出件生成或发件前，必须加载 `config/qa_controls.yaml` 并阅读精炼入口 `docs/15_qa_lessons_learned.md`。新问题的完整经过写入 `docs/qa_archive/`，同时映射或新增 `QA-*` 控制；自动控制必须有规则代码和回归测试，非确定性检查必须有 manual gate 和留存 artifact，禁止只追加自然语言经验。
14. **深度扩池双阶段**：当国家级/场景级发现无法满足候选池目标时，必须依次执行“扩大来源面”和“城市级精搜”。扩大来源面要覆盖官方/监管、运营方/业主、行业目录、项目方/设计施工方、会议/酒店/体育等垂直目录及本地语言来源；城市级精搜要按首都、核心经济城市、区域中心、交通/旅游节点和低覆盖城市建立 city × scene × primary metric 矩阵。两阶段都必须先查 KnownOpportunityIndex 和已抓 URL、先 search 留存 manifest 再少量 scrape；本地 Firecrawl 每条 agent 链最多同时运行一个 subprocess。数量目标不得覆盖实体、运营状态、坐标和量化主指标质量门，无法达标时输出真实 shortfall 与可执行补证清单。

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
