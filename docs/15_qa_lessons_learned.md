# iSite2 QA Active Controls

这是项目级精炼 QA 入口。完整事故历史保留在
[`docs/qa_archive/2026_history_through_2026-08-10.md`](qa_archive/2026_history_through_2026-08-10.md)，
机器可读的执行归属、规则和测试映射在 `config/qa_controls.yaml`。

## 使用规则

1. 扫网、QA、输出或发布前加载 `config/qa_controls.yaml`，只执行本轮适用控制。
2. `critical/high` 自动控制失败时阻断发布；人工控制必须在报告中留存 artifact 路径。
3. 新事故的完整经过写入 `docs/qa_archive/`，并映射或新增一个 `QA-*` 控制。
4. 自动控制必须同时有规则代码和回归测试；视觉、合规、运维检查必须有 manual gate 和 artifact。
5. 禁止只向本文件追加自然语言事故表。

## 自动阻断控制

- `QA-SCAN-001`：全量候选池不得被 Top N 截断。
- `QA-SCAN-002`：本地语言、本地渠道和 search manifest 先于英文兜底。
- `QA-FIRE-001`：自动化 Firecrawl 只走本地 wrapper，并留存可复用结果。
- `QA-ACQUIRE-001`：显式注入的抓取 provider 不得被环境中新安装的可选 adapter 覆盖。
- `QA-IDENTITY-001`：抓取和入库前执行物业身份、别名、URL、坐标和 identity-key 去重。
- `QA-SEARCH-001`：多语种名称搜索不得破坏非拉丁文字，结果返回匹配名称和类型。
- `QA-ENTITY-001`：城市和坐标必须指向观测物业，不得是国家、大区、QID 或句子。
- `QA-ENTITY-002`：城市展示和 identity 使用官方确定性映射的规范主城市；原 commune/district/wilaya 保留在审计字段，未映射或冲突时阻断逐国发布。
- `QA-EVIDENCE-001`：使用场景一级量化指标，并保留字段、来源、层级和日期。
- `QA-TRAFFIC-001`：年份、P81/P1083、线路名、日流量和单场活动不得污染年客流。
- `QA-BUILD-001`：高价值证据不能证明室分建设状态。
- `QA-DERIVED-001`：仅在证据 hash 或模型版本变化时定向刷新衍生信息。
- `QA-PIPELINE-001`：最后一次证据/图片同步后再运行 derived、Traffic、本地化和预聚合。
- `QA-LEAD-001`：指定线索只能豁免主指标，且保持 `Survey First` 和可执行补证动作。
- `QA-IMAGE-001`：拒绝破图、占位图、Logo、地图和无关图片。
- `QA-IMAGE-002`：图片单字段变化独立于 evidence hash 同步到 overlay/active。
- `QA-IMAGE-004`：Next.js 等图片代理 URL 入库前必须解码为可持久访问的绝对原图 URL。
- `QA-IMAGE-005`：无图候选仅在三次独立图片搜索均留存失败审计后可上线，并在 UI 保持明确缺图状态。
- `QA-LOCAL-001`：普通 GET 不翻译；缓存必须持久化、可用且语言方向正确。
- `QA-LOCAL-002`：翻译保留数字、年份、URL、canonical key、单位、来源名和物业名。
- `QA-PUBLISH-001`：public 预聚合必须达到 cache miss/pending/error 全零。
- `QA-PUBLISH-002`：UI/public 统计使用 latest view，不使用历史 `properties` 总行数。
- `QA-PUBLISH-003`：小批更新使用 property-scoped delta，不得替换无关历史行。
- `QA-PUBLISH-004`：候选时间相同时必须用 scan run 时间和稳定 ID 确定唯一 latest packet，禁止缓存门与发布集合漂移。
- `QA-REPORT-001`：国家报告按数据指纹、审计状态和文件哈希原子激活。
- `QA-UI-001`：API 失败显示可重试错误，不得伪装成 `No visible points`。
- `QA-UI-002`：地图上方透明布局容器不得截获标记事件，只有真实控件可接收指针操作。
- `QA-UI-003`：城市筛选列表的主数字必须使用候选数，不能误用地图可见点数。
- `QA-OUTPUT-001`：Excel、PPT、UI 共用 canonical metric、结论和推荐总数，但按产物独立验收；Excel-only 发布不得被 PPT 图片门阻断。
- `QA-OUTPUT-003`：报告激活前按“场景+指标”执行量级上限；机场实际吞吐与规划容量分开验收，酒店单体客房数超过 5000 时阻断并回退到可信证据。
- `QA-OUTPUT-004`：确认达标统计的零项与非零项必须使用同一物业级证据标准；缺少全国可批量联结表不能直接写成 0，须先做逐物业补查，所有计数都必须能下钻到明细来源。
- `QA-REVIEW-001`：Review Queue 必须写明核验对象、来源和具体动作。
- `QA-NETWORK-001`：投诉与 Ookla 是独立网络 Proxy，不得证明 DAS 状态。
- `QA-REQUEST-001`：提单、联系邮箱和交付记录必须留在独立运营库，不得进入 public snapshot 或预聚合。
- `QA-REQUEST-002`：只有精确提单编号和管理 token 的显式审批可以推进执行状态，禁止模糊自然语言自动批准。
- `QA-REQUEST-003`：提交、更新通知和完成邮件必须幂等；扫网/功能未验证上线、PPT 未通过 QA 时禁止交付。
- `QA-REQUEST-004`：多场景扫网只使用一个合计目标并逐场景报告实际新增；自定义国家或城市必须先核验行政实体，无法确认时阻断执行。

## Manual Gates

Manual 不等于口头检查。每项都必须在本轮报告中记录 artifact 路径。

- `QA-SCAN-003`：留存“扩大来源面”和 city × scene 搜索矩阵，以及真实 shortfall。
- `QA-SCAN-004`：留存逐场景 before/after；单一场景占新增多数时必须解释。
- `QA-IMAGE-003`：留存 contact sheet 或逐图审核清单，确认图片属于目标物业。
- `QA-OUTPUT-002`：留存 Excel/PPT 渲染预览，检查真图、全名、可读字号和越界。
- `QA-OUTPUT-004`：留存国家×场景统计与物业明细的逐格回算结果；0 项须附补查结论，非零项须通过同一门槛、实体、单位和来源审计。
- `QA-COMPLIANCE-001`：留存 robots、登录、验证码、付费墙、条款和 PII 判断。
- `QA-INPUT-001`：分别审计表格显示文本、公式、数值坐标和真实 hyperlink target。
- `QA-OPS-001`：留存 UI/API、PostgreSQL、磁盘、内存/swap、容器和 OOM 检查。

## 新问题闭环

1. 将事故细节追加到按日期组织的归档文件。
2. 映射已有 `QA-*`，或在 `config/qa_controls.yaml` 新增控制。
3. 可确定性复现的问题必须补实现和测试；不可确定性问题必须定义人工 artifact。
4. 运行 `tests/test_qa_controls.py`。引用缺失、ID 未进入精炼入口或 text-only 控制都会失败。
