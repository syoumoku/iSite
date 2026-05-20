# Web-first / Firecrawl-fill 扫网策略

## 目标

后续国家扫网默认先用 ChatGPT 网页搜索和公开结构化入口确认候选点，再用 Firecrawl 做缺口补齐，最大限度节省 Firecrawl API 消耗。

## 标准链路

0. **场景优先级先排队**
   - 主扩池优先：地标级交通枢纽（机场、地铁站、火车站）、会展中心、奢华酒店/MICE、体育场、办公/政府楼宇。
   - 次级扩池：购物中心、邮轮港口等仍保留，但排在主扩池之后。
   - 补充扩池：医院和大学默认降级，仅在主扩池阶段明显不足、用户点名、或已有强公开线索时再展开。

1. **ChatGPT/Web 搜索先发现**
   - 用国家 + 场景 + 主指标搜索候选池，例如 airport passenger traffic、central station ridership、convention exhibition area、luxury hotel ballroom capacity、stadium capacity、office tower GFA。
   - 优先记录搜索结果里的物业名、城市、来源 URL、公开摘要和可能的主指标。
   - 明显列表页、国家统计页、城市页只作为线索，不直接作为物业候选。

1A. **Firecrawl 目录页优先发现**
   - 候选扩池批次默认先找国家级/场景级目录页，包括机场/港口/车站列表、体育场馆目录、会展/会议场地目录、shopping mall directory、Grade A/office tower directory、酒店 MICE/meeting venue directory。
   - 第一跳只运行 Firecrawl search，默认不带 `--scrape`；输出必须保存到 `.firecrawl/{region_or_country}/directory_pages/`，并生成目录 manifest，记录 query、URL、标题、是否采纳、跳过原因。
   - 自动化任务必须通过 `PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "query" --limit 10 --json -o .firecrawl/{region_or_country}/directory_pages/search.json` 调用 Firecrawl CLI，避免继承 Codex shell 的本机代理变量。需要诊断时先跑 `scripts/run_firecrawl_cli.py --print-env`。
   - 目录页只作为入口。只有能稳定列出多个物业级对象、有详情页或可二跳补主指标、且不明显是广告/OTA 噪声页的目录，才进入 scrape。
   - 每国每场景先 scrape 2-3 个高质量目录页；抽出的候选再过已知物业、已知 URL、bbox、真实图片和场景主指标质量门。

2. **公开结构化入口补基础字段**
   - Wikipedia/Wikimedia、官方公开 API、政府/运营方页面摘要优先用于坐标、图片、页面日期和基础主指标。
   - 体育场和会展中心的容量类硬指标先读 MediaWiki/Wikipedia infobox：`capacity`、`seating capacity`、`seating_capacity`、`seats` 等字段映射为体育场 `seat_count`，会展 `capacity` 映射为 `peak_event_capacity`。这些字段必须保留原始页面 URL、语言版本和抓取日期；单一百科源只能作为 Tier 3 直接证据，后续优先用 DBpedia、OSM tag、官网或场馆目录做第二来源交叉验证。
   - 坐标/地图补齐必须在抓取正文前完成；无法落到目标国家 bbox 的结果不再 scrape。
   - 公开图片必须带 `url/source_url/source_name/source_date/license` 元数据，否则不进入 UI 可见候选。

3. **Firecrawl 只做缺口补齐**
   - 仅当 Web 搜索和公开结构化入口无法提供足够正文、PDF/JS 页面需要抽取、或需要精确页面内容时调用。
   - 默认关闭 search-result scrape，先拿 URL 和标题，过已知物业/已知 URL/地图预过滤后再 scrape。
   - 同 URL 已抓取且内容 hash 未变化时不重复 Firecrawl。
   - 对目录页批次，先保存 search 结果和目录 manifest，再 scrape 被采纳目录；不得出现“消耗 credit 但搜索结果未留存”。

4. **质量门后同步活跃库**
   - 候选必须有物业级坐标、场景主指标证据、公开图片和来源日期。
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

其中 `firecrawl_requests_saved_estimate` 包含已知物业/URL跳过，以及坐标/国家预过滤阻止的抓取。
