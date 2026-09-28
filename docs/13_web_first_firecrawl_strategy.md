# Web-first / Firecrawl-fill 扫网策略

## 目标

后续国家扫网默认先用 ChatGPT 网页搜索和公开结构化入口确认候选点，再用 Firecrawl 做缺口补齐，最大限度节省 Firecrawl API 消耗。

## 标准链路

0. **场景优先级先排队**
   - 主扩池优先：地标级交通枢纽（机场、地铁站、火车站）、会展中心、奢华酒店/MICE、体育场、办公/政府楼宇。
   - 次级扩池：购物中心、邮轮港口等仍保留，但排在主扩池之后。
   - 补充扩池：医院和大学默认降级，仅在主扩池阶段明显不足、用户点名、或已有强公开线索时再展开。

0A. **国家本地化搜索 Profile**
   - 每个国家扫网或补证前，先建立并留存 `.firecrawl/{country_or_run}/localization_profile.json`，不得跳过。
   - Profile 必须包含：主要官方语言、实际搜索语言优先级、主要使用的搜索引擎/搜索渠道、搜索引擎判断来源和日期、本地域名/TLD、政府/运营方域名、各场景主指标本地术语、英文兜底条件。
   - 搜索引擎份额会变化，不能只凭记忆硬编码；如果已有 country profile 超过 12 个月未核验，必须重新用公开来源或当轮搜索结果核验后再使用。
   - 优先用官方语言 + 本地域名 + 本地指标术语搜索。英语查询只在三种情况下使用：当地官方来源本身使用英语、官方语言搜索无有效结果并已记录失败原因、或用于第二来源交叉验证。
   - 如果 Firecrawl 不能直接选择某个本地搜索引擎，仍需把本地化意图落到 query 和 manifest：使用官方语言关键词、本地域名约束、当地机构名称；也可以先用 ChatGPT/Web/浏览器从主要本地搜索引擎识别 URL，再用 Firecrawl scrape 少量已筛选 URL。
   - 每条 search artifact 和目录 manifest 必须记录 `language`、`search_channel`、`query`、`target_scene`、`adopted/skipped_reason`。没有本地语言 search artifact 的国家扫网不能进入 QA 通过状态。

1. **ChatGPT/Web 搜索先发现**
   - 用国家 + 场景 + 主指标搜索候选池，先使用本地化术语，例如机场客流、车站客流、会展面积、酒店房间数/宴会厅容量、体育场座位数、办公楼 GFA/NLA 等在当地官方语言中的表达。
   - 优先记录搜索结果里的物业名、城市、来源 URL、公开摘要和可能的主指标。
   - 明显列表页、国家统计页、城市页只作为线索，不直接作为物业候选。

1A. **Firecrawl 目录页优先发现**
   - 候选扩池批次默认先找国家级/场景级目录页，包括机场/港口/车站列表、体育场馆目录、会展/会议场地目录、shopping mall directory、Grade A/office tower directory、酒店 MICE/meeting venue directory。
   - 第一跳只运行 Firecrawl search，默认不带 `--scrape`；输出必须保存到 `.firecrawl/{region_or_country}/directory_pages/`，并生成目录 manifest，记录 query、URL、标题、是否采纳、跳过原因。
   - 自动化任务必须通过 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "query" --limit 10 --json -o .web_evidence/{region_or_country}_{run}/local_firecrawl_round1/search.json` 调用本地 Docker Firecrawl，避免继承 Codex shell 的本机代理变量并确保搜索结果落盘。默认不读取 API key、不消耗云端 credits；云端模式仅允许人工显式传 `--deployment cloud`。需要诊断时先跑 `scripts/run_firecrawl_cli.py --print-env`。
   - 目录页只作为入口。只有能稳定列出多个物业级对象、有详情页或可二跳补主指标、且不明显是广告/OTA 噪声页的目录，才进入 scrape。
   - 每国每场景先 scrape 2-3 个高质量目录页；抽出的候选再过已知物业、已知 URL、bbox 和场景主指标质量门。真实图片作为 UI 增强项单独校验：坏图必须换源，缺图不阻断真实物业入库。

2. **公开结构化入口补基础字段**
   - Wikipedia/Wikimedia、官方公开 API、政府/运营方页面摘要优先用于坐标、图片、页面日期和基础主指标。
   - 体育场和会展中心的容量类硬指标先读 MediaWiki/Wikipedia infobox：`capacity`、`seating capacity`、`seating_capacity`、`seats` 等字段映射为体育场 `seat_count`，会展 `capacity` 映射为 `peak_event_capacity`。这些字段必须保留原始页面 URL、语言版本和抓取日期；单一百科源只能作为 Tier 3 直接证据，后续优先用 DBpedia、OSM tag、官网或场馆目录做第二来源交叉验证。
   - 坐标/地图补齐必须在抓取正文前完成；无法落到目标国家 bbox 的结果不再 scrape。
   - 有公开图片时必须带 `url/source_url/source_name/source_date/license` 元数据，并通过 HTTP/代理可打开校验；打不开的图先用 Firecrawl 搜图换源。缺图点位也进入 Firecrawl 搜图补齐队列；实在找不到可打开真图时清空或保持空图片字段但保留候选。

3. **Firecrawl 只做缺口补齐**
   - 仅当 Web 搜索和公开结构化入口无法提供足够正文、PDF/JS 页面需要抽取、或需要精确页面内容时调用。
   - 默认关闭 search-result scrape，先拿 URL 和标题，过已知物业/已知 URL/地图预过滤后再 scrape。
   - 同 URL 已抓取且内容 hash 未变化时不重复 Firecrawl。
   - 对目录页批次，先保存 search 结果和目录 manifest，再 scrape 被采纳目录；不得出现“消耗 credit 但搜索结果未留存”。

4. **质量门后同步活跃库**
   - 候选必须有物业级坐标、场景主指标证据和来源日期；公开图片优先补齐，但不作为真实物业候选入库阻断条件。
   - 通过质量门的 overlay 候选同步到活跃库；疑似重复进入 Review Queue，不自动合并。

## Firecrawl 触发条件

- 官方或运营方页面存在，但 ChatGPT/Web 只返回摘要，缺正文指标。
- PDF/JS 页面需要抽取指标文本。
- 需要核验页面内容是否包含与候选物业一致的主指标。

## 审计指标

每轮报告继续记录：

- `known_property_skipped_count`
- `known_url_skipped_count`
- `firecrawl_requests_saved_estimate`
- `firecrawl_search_count`
- `firecrawl_fetch_count`
- `firecrawl_credits_used`
- `localization_profile_path`
- `localized_search_count`
- `local_language_search_count`
- `local_source_evidence_count`
- `english_fallback_count`

其中 `firecrawl_requests_saved_estimate` 包含已知物业/URL跳过，以及坐标/国家预过滤阻止的抓取。

## 本地化 QA 验收规则

国家级扫网、单国补证和多国批量扫网都按以下规则验收：

- **硬性失败**：缺少 `.firecrawl/{country_or_run}/localization_profile.json`，或 profile 未记录官方语言、搜索语言优先级、主要搜索引擎/搜索渠道、本地域名和本地指标术语。
- **硬性失败**：manifest/search artifact 没有 `language`、`search_channel`、`query`、`adopted/skipped_reason`，导致无法复盘本地化链路。
- **硬性失败**：直接使用英文或国际目录作为第一搜索路径，且没有记录“本地官方语言搜索失败”或“该国官方来源主要以英文发布”的依据。
- **硬性失败**：搜索引擎/搜索渠道 profile 超过 12 个月未核验，仍继续 live Firecrawl 批量抓取。
- **需进入 Review Queue**：某场景只找到英文/国际来源，未找到官方语言来源；Review action 必须写清“用某官方语言 + 某本地指标词 + 某本地域名/搜索渠道补查某主指标”。
- **通过条件**：每个目标场景至少有一次官方/本地语言搜索尝试；被采纳的硬证据优先来自本地官方、运营方、行业协会、政府统计或本地域名来源；英文来源只做交叉验证或兜底，并在报告中统计 `english_fallback_count`。
