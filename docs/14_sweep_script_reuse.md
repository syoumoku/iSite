# Sweep Script Reuse Guide

本文件固化扫网脚本整理规则：后续批量扫网、证据补足、图片替换、去重入库应优先复用现有入口，通过参数、外部输入文件和配置控制范围，不再新增某个国家或某次任务专属脚本。

## QA 经验前置

- 每次 QA 检查、扫网收尾、输出件生成或发件前，必须先阅读 `docs/15_qa_lessons_learned.md`。
- 本轮新问题的完整经过写入 `docs/qa_archive/`，并映射或新增 `config/qa_controls.yaml` 中的 `QA-*`；精炼入口仅同步控制索引，不追加完整事故历史。
- 如果同类问题重复出现，优先固化为配置、脚本参数、质量门或测试，不只写人工提醒。

## 标准动作链

1. 先做计划，不消耗 Firecrawl：`scripts/run_structured_first_backfill.py`
   - 用 `--countries`、`--scenes`、`--max-properties` 控制范围。
   - 只输出计划、缺口和 Firecrawl 估算，不发起 live call。

1A. 建立国家本地化搜索 Profile。
   - 每个国家在 live search / Scrapling/Firecrawl 前必须生成或更新 `.web_evidence/{country_or_run}/localization_profile.json`；历史 `.firecrawl/` profile 可读取但新产物写入 `.web_evidence/`。
   - Profile 必须记录主要官方语言、实际搜索语言优先级、主要使用的搜索引擎/搜索渠道、判断来源与日期、本地域名/TLD、政府/运营方域名、场景主指标本地术语、英文兜底条件。
   - 主要搜索引擎/搜索渠道属于时效性信息；如果本地 profile 超过 12 个月未核验，先用公开来源或当轮搜索结果重新确认，不得凭旧记忆直接执行。
   - 后续所有 search manifest 必须把 `language` 和 `search_channel` 写入记录。Firecrawl 不支持指定本地搜索引擎时，也要通过官方语言 query、本地域名约束和本地指标术语体现本地化；必要时先用 ChatGPT/Web/浏览器从本地搜索引擎识别 URL，再用 Firecrawl scrape 已筛选页面。

2. 先跑免费/结构化扩池：`scripts/run_web_first_structured_landmark_expansion.py`、`scripts/run_public_structured_growth_pass.py`
   - 适合机场、体育场、会展、mall、办公等可由公开结构化源发现的场景。
   - 默认依赖已知国家索引和已知 URL 去重，避免重复候选和重复抓取。
   - 每轮开始前统计目标国家现有场景分布，并把新增配额优先分配给低于该国场景中位数的高价值场景。单一场景原则上不得占本轮新增候选的 50% 及以上；酒店不得因目录易得而挤占交通枢纽、会展、商场、体育场、办公和机场的增量配额。只有用户明确指定单场景时才允许突破该限制，并在报告中披露原因。

3. Search manifest + Scrapling 目录页优先扩池。
   - 第一跳用 ChatGPT/Web/浏览器/本地搜索渠道、免费结构化源、SerpAPI 或显式 Firecrawl fallback 找国家级/场景级目录页；结果必须先保存为 search manifest。
   - Scrapling 不负责搜索整个互联网，不批量抓 Google/Bing SERP；它只读取已筛过、robots 允许的页面 URL。
   - Search manifest 必须包含 `language/search_channel/query/title/url/snippet/adopted/skipped_reason/target_scene`，作为后续零 credit 复放输入。
   - 自动化任务禁止裸跑 `firecrawl ...`；统一用 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "query" --limit 10 --json -o .web_evidence/.../local_firecrawl_round1/search.json`。该入口项目级默认连接本地 Docker Firecrawl，不读取 API key、不消耗云端 credits，并清理自动化 shell 继承的本机代理变量；云端模式只有人工明确要求时才允许传 `--deployment cloud`。
   - 只有用户明确允许 Firecrawl fallback 时才执行上一条；默认流程不消耗 Firecrawl credits。
   - 若怀疑网络/代理问题，先运行 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py --print-env` 和一个 `--limit 1` smoke search，并把结果落到 `.firecrawl/diagnostics_*/`。
   - 每轮必须保存原始 search 输出和目录 manifest；manifest 记录 query、URL、采纳/跳过、场景、国家和后续动作。
   - 每国每场景先用 Scrapling fetch/crawl 2-3 个高质量目录页，静态失败或正文过短才升级 Dynamic；公开页面轻量反爬且 robots 允许时可升级 Stealthy；检测到登录、验证码、付费墙立即写 Review Queue，不继续绕过。
   - 目录页不直接入库；只有带物业级坐标和场景主指标证据的真实候选才进入 curation。图片是独立增强链：坏图必须替换，缺图可保持空字段进入 active，不能为了图片目标使用无关或不可打开的素材。

3A. 普通扩池仍不足时，执行项目级“扩大来源面 -> 城市级精搜”，不得新建国家专属脚本。
   - **扩大来源面**：按本地政府/监管与统计、业主/运营方、行业协会和垂直目录、开发商/建筑师/工程顾问/施工方项目页、活动组织方/旅游机构/本地商业目录、国际聚合兜底的顺序扩展网站范围。每个来源渠道和域名类别都写入 manifest；扩大来源不等于降低证据等级。
   - **城市级精搜**：先读取 active 场景数量，只为未达标场景生成 `city × scene × localized primary metric × source hint` 矩阵。城市覆盖首都/核心都市、主要经济人口中心、区域中心、机场港口旅游会展节点和低覆盖城市；城市别名先去重。
   - 每一条查询先经过 KnownOpportunityIndex、已知 URL、exact-query cooldown、国家 bbox 和场景身份预过滤。先 search 落盘，再对少量采纳详情页 scrape；不能把城市矩阵直接展开成无上限抓取。
   - 本地 Firecrawl 每条 agent 链同时最多一个 subprocess；多个 agent 也必须遵循任务给定的总并发上限。API 异常或队列拥塞时保留失败 manifest 并暂停新请求，不能把失败误报为无候选。
   - 结束时按 active repository 相对 baseline 的净增量验收。报告必须含逐场景目标、active 新增、shortfall、城市覆盖、来源渠道分布、已知物业/URL 跳过数、Firecrawl 本地请求数/云 credits，以及每条被拒线索的具体补证动作。
   - 数量目标不能覆盖四个硬门：真实独立物业、当前运营状态、物业级坐标、场景允许的量化主指标。继续搜索只返回重复、停运/在建、泛化实体或无量化指标时，应停止并报告真实可行性上限，不得拆分同一物业或伪造 proxy 凑数。

4. 导入 agent/Firecrawl 结构化结果：`scripts/run_apac_other_scene_agent_import.py`
   - 文件名保留历史 APAC 名称，但入口已参数化，可用于任意国家批次。
   - 用 `--input-files` 传入结构化 JSON，用 `--source-type`、`--source-date` 标识批次来源。
   - 候选仍必须通过主指标、身份去重和 curation gate。需要保留无图但证据合格的候选时显式使用 `--allow-missing-image`；图片门继续替换坏图或补图，但找不到合格图片时保持空图片并保留 active 候选。

5. 对已有候选补证据：`scripts/run_free_structured_evidence_backfill.py` 优先，`scripts/run_full_evidence_backfill_firecrawl.py --provider scrapling --search-manifest-path ...` 兜底。

6. 证据池刷新后，用 GPT 聚合刷新衍生信息：本地个人项目默认走 Codex OAuth，执行 `scripts/run_gpt_derived_info_refresh.py --provider codex-oauth`；只有明确需要 API Key 通道时才显式使用 `--provider gpt`。
   - 旧 Firecrawl 文件名保留兼容；默认 provider 是 Scrapling，写 `.web_evidence/full_evidence_backfill/` audit log/resume 输出，报告中 `firecrawl_*` 统计应为 0。
   - 只有 `--provider firecrawl` 或 `--provider auto --allow-firecrawl-fallback` 才设置并消耗 `--credit-budget`。
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
   - 图片补齐默认从候选/证据页用 Scrapling 抽 `og:image`、`twitter:image`、JSON-LD image 和正文图片；只有 `--allow-firecrawl-fallback` 才执行 Firecrawl image search。

8. 入活跃库前必须校验 city/property 匹配。
   - `city` 只能是观测到的城市/地方名，不得填国家、省/州/大区、区县、atoll、行政区、Wikidata QID 或任何默认值。
   - 发现城市层级污染时，先修正 overlay 中的 `city`，同步更新 `property_identity_key`，再按 identity key 合并重复候选。
   - 无法确认具体城市/地方名的候选进入 Review Queue，不能进入 active repository、地图或导出。

9. 质量门通过后同步活跃库。
   - overlay 中通过质量门的候选必须同步到 active repository。
   - 疑似重复进 Review Queue，不自动合并。

10. QA 收尾前复盘经验库。
   - 读取 `docs/15_qa_lessons_learned.md`，按快速前置清单抽查本轮扫网、证据、active 同步、Firecrawl 留存和输出件。
   - 本轮新增问题先写入 `docs/qa_archive/` 并落实相应控制、测试或人工 artifact，再写 QA 总结或发件。

## 脚本分层

### 可复用入口

- `scripts/run_structured_first_backfill.py`：结构化优先扫网计划。
- `scripts/run_web_first_structured_landmark_expansion.py`：web-first/结构化地标扩池。
- `scripts/run_public_structured_growth_pass.py`：免费公开结构化源扩池。
- `scripts/run_free_structured_evidence_backfill.py`：免费结构化证据补足。
- `scripts/run_full_evidence_backfill_firecrawl.py`：通用 public web 证据兜底，默认 `--provider scrapling`，支持 search manifest、resume 和 `.web_evidence` 审计；Firecrawl 只作为显式 fallback。
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
- 不允许跳过国家本地化搜索 Profile；没有官方语言、主要搜索引擎/搜索渠道、本地域名和本地指标术语的国家扫网，只能做 dry-run 计划，不能进入 live Firecrawl 批量抓取。
- 不允许扫网结果给 `city` 或其他实体字段填默认值；`city` 也不能用省/州/大区/区县/atoll/municipality/department/region 等行政层级顶替。质量门会将这类候选标为 `blocked_quality`，修正时必须同时更新 identity key 并去重。
- 不用规则直接替代衍生判断。访问量估算、容量估算、推荐理由、下一步计划必须优先由 GPT 基于单物业客观证据包聚合生成；规则只做字段校验、主指标质量门和无 key 环境下的 smoke fallback。
- 原始证据清洗允许引入 GPT 作为结构化抽取辅助，但只能在规则解析失败、文本歧义或多语言复杂表达时触发；GPT 必须输出固定 JSON schema，包含原文片段、字段名、数值、单位、是否可作场景主指标、置信度和解释。结果按 `source_url + text_hash + schema_version` 缓存，不直接覆盖主表硬证据；低置信或与规则校验冲突的结果进入 Review Queue。
- 原始证据 GPT 清洗统一接入 `run_pending_evidence_curation`，通过 `ISITE2_RAW_EVIDENCE_GPT_CLEANING=1` 启用；默认 provider 为 `codex-oauth`，可用 `ISITE2_RAW_EVIDENCE_GPT_PROVIDER=auto|gpt|codex-oauth` 切换。该层只改写 candidate draft/overlay 证据并写入 `assumption_note` 留痕，不直接写 active 主表。
- 机场 `gateway_role`、`hub_role` 等描述性角色证据不得作为机场主指标；GPT 输出也必须经过硬主指标质量门，无法命中客流/航站楼容量等量化指标时打 `low_primary_metric_evidence`。
- 年客流、日客流等直接访问量硬主指标必须锚定客观证据解析值；GPT 只能解释和生成推荐理由，不能用 `2024`、`1995` 等年份或未缩放的 `12.8` 覆盖 `37.6 million`、`12.8 million` 等硬指标。
- 数字解析必须过滤纯年份，并识别 `970.349` 这类千位点分隔格式；只有解析到真实量化主指标时才能刷新 `annual_visits_est`、容量估算和需求链路。
- 地铁/铁路枢纽的 `line_count` 可以来自显式计数后的 Wikidata P81 线路关系，例如 `2 rail/metro lines connected per Wikidata P81 statements` 可作为 `line_count=2`；但解析器不得把 `P81` 属性编号识别成 81，也不得把 `Line 1`、`línea 1` 等线路名称当作线路数量。`line_count` 是交通枢纽主指标，但不能单独推导 `annual_visits_est`。
- 体育场/会展容量补证据时，Firecrawl 前先走免费结构化源：优先用 MediaWiki/Wikipedia infobox 的 `capacity`、`seating capacity`、`seating_capacity`、`seats` 抽取体育场 `seat_count` 或会展 `peak_event_capacity`，并保留页面 URL、语言版本和日期。该证据是可量化硬指标但来源等级仍按 Tier 3 处理，单源命中后下一步动作应是补 DBpedia、OSM、官网或场馆目录的第二来源，不要直接重复 Firecrawl 搜索同一指标。
- 办公/政府楼宇不得把通用 `skyscraper/tower` 当作办公身份。结构化源扫网只接受办公楼、总部、政府楼宇等明确用途入口；楼层数/塔高只能作为弱辅助证据，不能替代 `office_nla`、`office_gfa`、楼宇等级、租户/HQ 密度等场景主指标。
- GPT 衍生刷新不得把自然语言解释写入结构化字段名（如 `area_metric_name`）；字段名必须来自已选场景指标，解释只能进入 `assumption_note`、`inference_basis` 或 `inference_chain`。
- 本地 OAuth 模式复用 `codex login` 的登录态，不把 Codex token 写入项目文件；如果 `~/.codex/auth.json` 不存在，先运行 `codex login`。
- 新增扫网任务或存量证据 curation 完成后必须触发一次衍生信息刷新，统一走 `isite2.growth.derived_refresh_trigger.refresh_derived_after_scan` 标准 hook；如需临时关闭，显式设置 `ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=0`，并在报告中说明原因。
- 标准后处理动作链固定为：`evidence refresh -> derived refresh -> Traffic V2 -> complaint/Ookla rollup -> network validation priority -> localization refresh -> public preaggregation`。Traffic V2 默认由 derived hook 定向触发，`annual_visits_est` 只映射 V2 P50；投诉和 Ookla 只改变网络验证优先级，不得覆盖物业主指标、年客流或 Build Status。
- Traffic V2 独立补算统一复用 `scripts/run_traffic_v2_refresh.py`；投诉 artifact 入库复用 `scripts/run_property_complaint_refresh.py`；Ookla 季度或既有 artifact 入库复用 `scripts/run_ookla_network_refresh.py`。不得另写一次性脚本绕过 input hash、来源合规门、去重约束或 public feature flag。
- 本地默认 `ISITE2_DERIVED_REFRESH_PROVIDER=codex-oauth`，复用 Codex OAuth 登录态；只有明确需要 API Key 通道时才显式设置为 `gpt`。
- 衍生信息刷新必须先计算单物业 `evidence_package_hash`。同一物业同一证据包已有 GPT 决策时，只复用 `derived_refresh_status` 中的结构化决策写回当前 active run，不再次调用 GPT；只有证据新增/字段值刷新/来源刷新导致 hash 改变，或人工显式传 `--force`，才重新进入 GPT 分析。
- 衍生信息刷新必须逐物业提交；中途失败或人工停止时，已经处理完成的物业要可从 `derived_refresh_status` 继续恢复，不能出现 GPT/credit 已消耗但结果未留存。
- 衍生信息刷新完成后必须执行 active 数据完整性 gate，并在 `qa_after.data_integrity_gate` 留存结果。阻断项包括：`annual_visits_est` 被年份污染、机场角色类证据进入主指标、交通枢纽 `line_count` 推导访问量、显式 `line_count` 证据未写回主指标、Wikidata 属性编号（如 P81/P3872）被当作指标数值。扫网报告中若 `passed=false`，必须先修 active 库再继续批量扩展。
- 衍生信息刷新后必须预热本地化缓存，至少覆盖 `reason_to_recommend`、`next_action`、`primary_metric.display_text`、必要 `evidence.field_value`、`review.reason`、`review.next_action` 的英文与中文展示文本。标准 hook 会调用 `refresh_localization_cache_for_packets`；如需临时关闭，显式设置 `ISITE2_ENABLE_LOCALIZATION_REFRESH_AFTER_DERIVED=0` 并在报告中说明原因。普通 GET 接口只读 `localized_text_cache`，不得现场调用 GPT；跨语言缓存缺失时只能返回后端占位文案和 `cache_miss` 状态，不能把 raw 英文/中文当作另一 locale 的展示兜底。UI、Excel、PPT 只消费 API/后端 localizer 提供的 localized/display 字段，不做翻译或主指标文案拼接。
- 公开发布不是衍生刷新入口。发布前只做只读 freshness/gap gate：原始证据包 hash 未变化的物业必须复用既有衍生结果；只有本轮 raw/evidence 变更的 property ids 才允许定向刷新。若发现 latest active run 有空占位或完整性缺口，但无法证明原始数据已变更，发布应阻断并输出缺口清单，禁止为了发布而全量 `--force` 刷新。
- Excel/PPT 等对外输出不得直接取 `evidence[0]` 作为主要证据；必须按场景主指标、候选质量规则、数值化程度、来源等级和交叉校验状态选择最强客观指标。推荐理由必须直接复用 UI 同源的 `conclusion.reason_to_recommend`，输出层不得二次改写。机场 `gateway_role`、`terminal_role` 等角色描述只能做辅助证据，不能进入“主要证据”的主指标位置。
- 清真寺 `mosque` 是标准场景之一，主指标只接受可量化的面积和人流量链路：`mosque_area/gross_floor_area/prayer_hall_area` 与 `annual_visitors/annual_visits/daily_visitors/annual_footfall/footfall`。`worshipper_capacity/peak_prayer_attendance` 只能作为辅助或 proxy，`landmark_role/religious_role/tourism_role` 等描述性角色不得作为主指标。阿语国家扫清真寺前必须优先用本地术语 `مساحة المسجد`、`عدد الزوار`、`سعة المصلين` 与政府/宗教事务/旅游官方来源。
- 国家看网/扫网报告输出标准：当用户说“给我 xx 国的看网报告/扫网报告”且没有另行指定时，自动输出 Excel 与 PPT 的中英双语版。Excel 必须按年访问量和场景主指标排序，主表和各场景分表都必须在“物业点重要证据/Core Objective Evidence”右侧输出“主指标量化值/Primary Metric Value”，从当前选中的物业点重要证据中提取纯数值，便于 Excel 一键排序；默认使用项目级固定 First Class 推荐门槛：机场年旅客吞吐量 > 2,000,000；会展中心面积 > 25,000 平方米；地铁/交通枢纽换乘线路数 > 1；体育场座位数 > 20,000；商超 GLA > 120,000 平方米；写字楼楼高 > 150 米；酒店房间数 > 150；医院床位数 > 450；大学单一实体校园在校生人数 >= 20,000；清真寺面积 > 20,000 平方米。大学系统、多校区合计、在线学生总数和年度新生录取数不得作为单一校园 enrollment 过门槛。推荐筛选阶段所有场景的主指标字段都要进入智能归一流程：先做规则预识别；存在 Codex OAuth 登录态且未显式关闭时，再用 GPT 将原始字段名/指标名/字段值归一到当前场景允许的 canonical metric key，并经过白名单和置信度校验；只有显式设置 provider 为 `gpt/openai` 时才使用 OpenAI API Key；GPT 不得改变固定推荐门槛或把模糊角色证据映射成硬主指标。未配置固定门槛的场景才回退为国家+场景第 10 名整数硬主指标。高亮推荐行，主表和各场景分表新增“是否被推荐”列且推荐点填 `yes`，并新增“推荐清单”页签；Excel 主表和场景分表不得使用隔行底色，只有推荐点整行使用淡黄色填色。PPT 默认输出一页精简版：上部压缩保留 KPI 与四步找楼流程，下部把每个场景的候选数、推荐数、推荐门槛、代表物业全称、同口径硬主指标、达标状态和真实图片融合进同一场景卡，不另设独立统计区或推荐区；Imagen 只用于视觉参考，最终必须以可编辑 PowerPoint 对象重建。原两页版保留为显式请求时的详细版。PPT 推荐点总数必须与 Excel 推荐清单/“是否被推荐=yes”同口径；PPT 与 Excel 中的物业点名称必须显示全称，不使用省略号截断。
- 多国标准输出件必须在一个进程内重复传 `--country` 调用 `scripts/generate_standard_country_report.py`。共享 payload、推荐决策和图片资产，只对场景数最多的国家做一次生成后冒烟 QA，其余国家并行生成；规则 QA 仍逐国执行，Codex OAuth GPT 审计全批合并为一次调用。内容哈希覆盖数据、配置、模板、生成/审计代码和 QA 经验库；哈希一致且上一版四件套与审计完整时直接复用，禁止无变化重算或重复下载图片。
- UI 图片展示必须走 `/map/hero-image` 本地代理缓存，不直接热链外部图片。批量图片修复复用 `scripts/run_apac_image_and_duplicate_gate.py --validate-existing-images`，先验证现有图片可打开并同步预热 `.tmp/isite2_hero_image_cache`；打不开的图片和缺图点位默认从候选/证据页用 Scrapling 抽图换源/补图，确实无可用真实图片时清空或保持空 `hero_image` 并保留物业点 active 可见，由 UI fallback 展示。只有预算受限或人工明确要求时才加 `--skip-missing-image-fill` 跳过缺图补齐。Wikimedia 图片由代理/校验层统一标准化为 `upload.wikimedia.org/wikipedia/commons/thumb/.../960px-...` 的缩略图 CDN 路径，SVG 才保留 PNG 缩略图后缀；429 限流只作为临时失败，不写入 7 天坏图缓存，也不触发重搜。图片候选必须通过真实 HTTP image 内容校验，过滤 Facebook/Instagram/lookaside/图库水印等低价值主机后才能写入 overlay/active。
- Firecrawl 图片补齐只作为显式 fallback：`scripts/run_apac_image_and_duplicate_gate.py --allow-firecrawl-fallback` 才使用 `--firecrawl-credit-budget 10000` 和保守的 `--estimated-image-search-credit-cost 4.0` 折算搜索上限；如用户另行给预算，以用户预算为准。每轮输出必须记录 `image_search_provider`、`firecrawl_fallback_enabled`、`firecrawl_credit_budget`、`budget_search_cap`、`requested_image_search_count_before_cap`、`image_search_count`、`image_replaced_count`、`kept_without_image_count`，确保额度消耗和结果留存可审计。
- 新增或替换物业图片时，优先选择官网、酒店集团、机场/场馆/会展官网、Tripadvisor、Cvent 等可打开的真实物业图；Wikimedia/Wikipedia 只能作为开放图片兜底来源，不能在有同等可打开官网/行业图时优先入库。
- Search manifest/URL 发现先经过已知 URL、已知物业、bbox、图片和主指标预过滤后再用 Scrapling fetch/crawl；Firecrawl search/scrape 只在显式 fallback 中使用。
- 后续外部信息搜集默认优先使用本地 Docker 部署的 Firecrawl/SearXNG：Firecrawl API `http://127.0.0.1:3002/v1/search|scrape`，SearXNG `http://127.0.0.1:8080/search?format=json`。每轮必须把 local Firecrawl search、SearXNG fallback、scrape 原始 JSON 和结构化 search manifest 写入 `.web_evidence/<country>_<run>/local_firecrawl_<round>/`，报告中声明本地调用不消耗云端 Firecrawl credits；如果本地 API 500/408/超时，保留失败 manifest 并切换可访问替代源，不得把失败当作“无证据”。
- 自动化 shell 里的 Firecrawl CLI 必须通过 `scripts/run_firecrawl_cli.py` 入口执行；入口默认 `ISITE2_FIRECRAWL_DEPLOYMENT=local`，连接 `http://127.0.0.1:3002/v1` 且云端 credits 固定为 0。项目内 Python provider 同样默认本地部署；只有人工明确要求云端时才设置 `ISITE2_FIRECRAWL_DEPLOYMENT=cloud` 或传 `--deployment cloud`。项目内 Python 调用必须使用 `firecrawl_subprocess_env()`，不要直接继承 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=127.0.0.1:7890` 这类本机代理变量。
- 候选扩池默认采用目录页优先路径；搜索结果必须先落到 `.web_evidence/` search manifest，再进入抽取/筛选，避免任何搜索或抓取消耗后无留存。历史 Firecrawl 输出继续保留在 `.firecrawl/`，但新产物默认写 `.web_evidence/`。
- 所有脚本报告必须保留：输入范围、国家本地化 profile 路径、语言/搜索渠道统计、跳过原因、写入数量、同步结果、Scrapling static/dynamic/stealth 抓取统计、Firecrawl fallback 调用/预算/节省估算。

## 项目级区域定义

- `North Africa` 是 iSite2 当前业务扫网分组，不等同于严格地理学北非。后续用户说“北非区域”时，默认使用以下国家范围，并保留中文别名到系统 canonical country 的映射。
- 国家范围：埃及 `Egypt`、埃塞俄比亚 `Ethiopia`、阿尔及利亚 `Algeria`、摩洛哥 `Morocco`、喀麦隆 `Cameroon`、塞内加尔 `Senegal`、科特迪瓦 `Cote d'Ivoire`、刚果/刚果布 `Congo`、马里 `Mali`、布基纳法索 `Burkina Faso`、几内亚 `Guinea`、冈比亚 `Gambia`、毛里塔尼亚 `Mauritania`、利比亚 `Libya`、突尼斯 `Tunisia`、刚果金 `Democratic Republic of the Congo`、加蓬 `Gabon`、乍得 `Chad`、赤道几内亚 `Equatorial Guinea`、中非 `Central African Republic`、佛得角 `Cape Verde`、贝宁 `Benin`。
- `刚果` 与 `刚果布` 都映射为 `Congo`；`刚果金` 映射为 `Democratic Republic of the Congo`。后端区域常量见 `src/isite2/growth/regional_targets.py` 的 `NORTH_AFRICA_COUNTRIES`；前端区域归属见 `ui/world_map/src/main.tsx` 的 `REGION_COUNTRIES["North Africa"]`。

## 后续整理优先级

1. 给 `run_public_structured_growth_pass.py` 增加 `--countries`，与其他入口一致。
2. 把 `run_cvent_apac_candidate_expansion.py` 的 `COUNTRY_META` 外置为配置文件。
3. 将历史硬证据批次脚本中的内嵌草稿迁移为 JSON/YAML 输入，再复用 agent import 或 evidence backfill。
4. 稳定后可做一次文件重命名：把仍带 `apac_` 的通用脚本改名为 `run_agent_scene_candidate_import.py` 和 `run_candidate_image_duplicate_gate.py`，同时保留兼容 wrapper。
