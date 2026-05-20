# Sweep Script Reuse Guide

本文件固化扫网脚本整理规则：后续批量扫网、证据补足、图片替换、去重入库应优先复用现有入口，通过参数、外部输入文件和配置控制范围，不再新增某个国家或某次任务专属脚本。

## 标准动作链

1. 先做计划，不消耗 Firecrawl：`scripts/run_structured_first_backfill.py`
   - 用 `--countries`、`--scenes`、`--max-properties` 控制范围。
   - 只输出计划、缺口和 Firecrawl 估算，不发起 live call。

2. 先跑免费/结构化扩池：`scripts/run_web_first_structured_landmark_expansion.py`、`scripts/run_public_structured_growth_pass.py`
   - 适合机场、体育场、会展、mall、办公等可由公开结构化源发现的场景。
   - 默认依赖已知国家索引和已知 URL 去重，避免重复候选和重复抓取。

3. Firecrawl 目录页优先扩池。
   - 第一跳只用 Firecrawl search 找国家级/场景级目录页，不直接大规模 `--scrape`。
   - 自动化任务禁止裸跑 `firecrawl ...`；统一用 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "query" --limit 10 --json -o .firecrawl/.../search.json`，该入口会清理自动化 shell 继承的本机代理变量，除非显式设置 `FIRECRAWL_TRUST_ENV_PROXY=1`。
   - 若怀疑网络/代理问题，先运行 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py --print-env` 和一个 `--limit 1` smoke search，并把结果落到 `.firecrawl/diagnostics_*/`。
   - 每轮必须保存原始 search 输出和目录 manifest；manifest 记录 query、URL、采纳/跳过、场景、国家和后续动作。
   - 每国每场景先 scrape 2-3 个高质量目录页，抽出物业级候选后再二跳补主指标。
   - 目录页不直接入库；只有带物业级坐标、真实图片和场景主指标证据的候选才进入 curation。

4. 导入 agent/Firecrawl 结构化结果：`scripts/run_apac_other_scene_agent_import.py`
   - 文件名保留历史 APAC 名称，但入口已参数化，可用于任意国家批次。
   - 用 `--input-files` 传入结构化 JSON，用 `--source-type`、`--source-date` 标识批次来源。
   - 候选仍必须通过真实图片、主指标、身份去重和 curation gate。

5. 对已有候选补证据：`scripts/run_free_structured_evidence_backfill.py` 优先，`scripts/run_full_evidence_backfill_firecrawl.py` 兜底。

6. 证据池刷新后，用 GPT 聚合刷新衍生信息：本地个人项目默认走 Codex OAuth，执行 `scripts/run_gpt_derived_info_refresh.py --provider codex-oauth`；生产/API key 环境可用 `--provider gpt`。
   - Firecrawl 脚本必须设置 `--credit-budget`，并保留 audit log/resume 输出。
   - 确实找不到场景主指标时，标低证据标签，不能用模糊证据替代主指标。
   - 新增扫网任务和存量证据更新的标准收尾动作是调用 `refresh_derived_after_scan`：API 创建扫描、regional loop、legacy Africa loop、overlay sync 都必须在候选落库或确认 overlay 已同步后触发一次衍生信息刷新。
   - 自动刷新仍先走 `evidence_package_hash` 门控；证据包未变化时只复用 `derived_refresh_status`，不重复消耗 GPT。
   - 即使 overlay sync 因 hash 已同步而跳过，也必须执行 derived refresh 的 hash 门控检查；脚本报告必须保留 `overlay_sync.derived_refresh`，不能只报告同步成功。
   - GPT 刷新必须逐物业 checkpoint：每个物业分析完成后立即写回衍生结果和 `derived_refresh_status`，禁止把整轮 GPT 调用包在一个长事务里。
   - 长批次可用 `--concurrency` 或 `ISITE2_DERIVED_REFRESH_CONCURRENCY` 做受控并发；默认值保持 1，避免自动扫网收尾时放大本地负载。

7. 入活跃库前跑图片/重复质量门：`scripts/run_apac_image_and_duplicate_gate.py`
   - 文件名保留历史 APAC 名称，但入口已参数化，可用于任意国家批次。
   - 用 `--countries ...` 指定范围，或用 `--all-countries` 处理 overlay 全量国家。
   - 可用 `--duplicate-alias-map` 传外部 alias/canonical 规则，不要把新国家别名继续写进脚本。

8. 入活跃库前必须校验 city/property 匹配。
   - `city` 只能是观测到的城市/地方名，不得填国家、省/州/大区、区县、atoll、行政区、Wikidata QID 或任何默认值。
   - 发现城市层级污染时，先修正 overlay 中的 `city`，同步更新 `property_identity_key`，再按 identity key 合并重复候选。
   - 无法确认具体城市/地方名的候选进入 Review Queue，不能进入 active repository、地图或导出。

9. 质量门通过后同步活跃库。
   - overlay 中通过质量门的候选必须同步到 active repository。
   - 疑似重复进 Review Queue，不自动合并。

## 脚本分层

### 可复用入口

- `scripts/run_structured_first_backfill.py`：结构化优先扫网计划。
- `scripts/run_web_first_structured_landmark_expansion.py`：web-first/结构化地标扩池。
- `scripts/run_public_structured_growth_pass.py`：免费公开结构化源扩池。
- `scripts/run_free_structured_evidence_backfill.py`：免费结构化证据补足。
- `scripts/run_full_evidence_backfill_firecrawl.py`：Firecrawl 证据兜底，带预算、resume 和审计。
- `scripts/run_apac_other_scene_agent_import.py`：结构化 agent 输出导入，历史文件名不代表只能扫 APAC。
- `scripts/run_apac_image_and_duplicate_gate.py`：图片真实度、别名合并、活跃库同步，历史文件名不代表只能扫 APAC。
- `scripts/run_regional_loop.py`、`scripts/run_continuous_regional_loop.py`：区域循环编排入口。

### Provider/批次桥接入口

- `scripts/run_cvent_apac_candidate_expansion.py`：Cvent 离线页面解析桥接。当前含 APAC 国家元数据，扩展前应把 provider 输入、国家元数据和目标阈值外置。
- `scripts/run_existing_scene_metric_enrichment.py`：已有候选补硬证据的批次脚本，含部分人工沉淀记录。后续新增证据应优先走外部输入或通用 backfill。
- `scripts/run_firecrawl_scene_hard_evidence_pass.py`、`scripts/run_scene_evidence_low_scene_pass.py`：历史硬证据/低场景补齐批次脚本。不要继续追加国家专属草稿，优先迁移为外部输入。

### Legacy Snapshot

- `scripts/run_apac_high_value_seed_pass.py`
- `scripts/run_apac_sri_lanka_other_scene_seed.py`
- `scripts/run_africa_loop.py`

这些脚本保留用于复现早期批次，不作为新扫网入口。新增国家、场景、候选或证据不应继续写入这些文件。

## 新扫网准入规则

- 不新增国家专属脚本；国家范围通过 `--countries`、overlay、source registry 或外部 JSON/YAML 输入控制。
- 不新增只服务某一轮对话的脚本；确有新来源时，先判断能否作为 provider bridge 参数输入。
- 不把候选清单硬编码进脚本；人工或 agent 产出的候选清单应保存为结构化输入文件。
- 不绕过 `KnownOpportunityIndex`、质量门、图片门、identity-key upsert 和 overlay sync。
- 不允许扫网结果给 `city` 或其他实体字段填默认值；`city` 也不能用省/州/大区/区县/atoll/municipality/department/region 等行政层级顶替。质量门会将这类候选标为 `blocked_quality`，修正时必须同时更新 identity key 并去重。
- 不用规则直接替代衍生判断。访问量估算、容量估算、推荐理由、下一步计划必须优先由 GPT 基于单物业客观证据包聚合生成；规则只做字段校验、主指标质量门和无 key 环境下的 smoke fallback。
- 机场 `gateway_role`、`hub_role` 等描述性角色证据不得作为机场主指标；GPT 输出也必须经过硬主指标质量门，无法命中客流/航站楼容量等量化指标时打 `low_primary_metric_evidence`。
- 体育场/会展容量补证据时，Firecrawl 前先走免费结构化源：优先用 MediaWiki/Wikipedia infobox 的 `capacity`、`seating capacity`、`seating_capacity`、`seats` 抽取体育场 `seat_count` 或会展 `peak_event_capacity`，并保留页面 URL、语言版本和日期。该证据是可量化硬指标但来源等级仍按 Tier 3 处理，单源命中后下一步动作应是补 DBpedia、OSM、官网或场馆目录的第二来源，不要直接重复 Firecrawl 搜索同一指标。
- 办公/政府楼宇不得把通用 `skyscraper/tower` 当作办公身份。结构化源扫网只接受办公楼、总部、政府楼宇等明确用途入口；楼层数/塔高只能作为弱辅助证据，不能替代 `office_nla`、`office_gfa`、楼宇等级、租户/HQ 密度等场景主指标。
- GPT 衍生刷新不得把自然语言解释写入结构化字段名（如 `area_metric_name`）；字段名必须来自已选场景指标，解释只能进入 `assumption_note`、`inference_basis` 或 `inference_chain`。
- 本地 OAuth 模式复用 `codex login` 的登录态，不把 Codex token 写入项目文件；如果 `~/.codex/auth.json` 不存在，先运行 `codex login`。
- 新增扫网任务或存量证据 curation 完成后必须触发一次衍生信息刷新，统一走 `isite2.growth.derived_refresh_trigger.refresh_derived_after_scan` 标准 hook；如需临时关闭，显式设置 `ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=0`，并在报告中说明原因。
- 本地默认 `ISITE2_DERIVED_REFRESH_PROVIDER=codex-oauth`，复用 Codex OAuth 登录态；生产/API key 环境可显式设置为 `gpt`。
- 衍生信息刷新必须先计算单物业 `evidence_package_hash`。同一物业同一证据包已有 GPT 决策时，只复用 `derived_refresh_status` 中的结构化决策写回当前 active run，不再次调用 GPT；只有证据新增/字段值刷新/来源刷新导致 hash 改变，或人工显式传 `--force`，才重新进入 GPT 分析。
- 衍生信息刷新必须逐物业提交；中途失败或人工停止时，已经处理完成的物业要可从 `derived_refresh_status` 继续恢复，不能出现 GPT/credit 已消耗但结果未留存。
- Excel/PPT 等对外输出不得直接取 `evidence[0]` 作为主要证据；必须按场景主指标、候选质量规则、数值化程度、来源等级和交叉校验状态选择最强客观指标。推荐理由必须直接复用 UI 同源的 `conclusion.reason_to_recommend`，输出层不得二次改写。机场 `gateway_role`、`terminal_role` 等角色描述只能做辅助证据，不能进入“主要证据”的主指标位置。
- Firecrawl 先 search/URL 发现，经过已知 URL、已知物业、bbox、图片和主指标预过滤后再 scrape。
- 自动化 shell 里的 Firecrawl CLI 必须通过 `scripts/run_firecrawl_cli.py` 入口执行；项目内 Python 调用必须使用 `firecrawl_subprocess_env()`。不要直接继承 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=127.0.0.1:7890` 这类本机代理变量。
- 候选扩池默认采用目录页优先路径；Firecrawl 搜索结果必须先落到 `.firecrawl/`，再进入抽取/筛选，避免 credit 消耗后无留存。
- 所有脚本报告必须保留：输入范围、跳过原因、写入数量、同步结果、Firecrawl 调用/预算/节省估算。

## 后续整理优先级

1. 给 `run_public_structured_growth_pass.py` 增加 `--countries`，与其他入口一致。
2. 把 `run_cvent_apac_candidate_expansion.py` 的 `COUNTRY_META` 外置为配置文件。
3. 将历史硬证据批次脚本中的内嵌草稿迁移为 JSON/YAML 输入，再复用 agent import 或 evidence backfill。
4. 稳定后可做一次文件重命名：把仍带 `apac_` 的通用脚本改名为 `run_agent_scene_candidate_import.py` 和 `run_candidate_image_duplicate_gate.py`，同时保留兼容 wrapper。
