# Codex / Agent 常用 Prompt 手册

先把仓库根目录设为工作目录，再粘贴任务。将尖括号内容替换成真实范围。不要在 prompt 中粘贴密码、API key、SSH 私钥或邮箱授权码；只给秘密文件路径。

Codex 会加载项目 `AGENTS.md`；长交接资料仍应在第一条任务中明确要求阅读。其他 agent 软件也使用同样阅读清单，不依赖特定插件。参考：[OpenAI 官方 AGENTS.md 文档](https://developers.openai.com/codex/guides/agents-md)（2026-09-28 核验）。

## 使用方法

每条任务写清四件事：**目标范围、允许操作、必须留存的证据、验收标准**。一次只处理一个明确批次；耗时任务按物业或阶段 checkpoint。需要长时间自动运行时，明确最大请求数、预算、并发、停止条件和通知条件。

下面每条 prompt 均继承本项目规则：全量不截断、证据优先、分场景、现网独立、推测留痕、本地化发现、复用脚本、QA 前置。不是让 agent 每次重新发明整套系统。

## Prompt 01：首次接手与环境验收

```text
你现在接手 iSite2。请阅读：
AGENTS.md、docs/16_handover.md、docs/18_deployment_runbook.md、
docs/19_handover_inventory.md、.agents/skills/isite2-scan/SKILL.md、
docs/14_sweep_script_reuse.md、docs/15_qa_lessons_learned.md、config/qa_controls.yaml。

先核对 Git 分支/未提交修改、Python/Node/Docker、数据库实际路径、前端构建、
Codex CLI、采集服务和现有自动化。只报告凭据是否可用，不输出秘密值。
按部署手册用独立演示库完成本地启动和健康检查，再核对真实数据交接情况。
不要修改生产数据、恢复旧自动化或重新执行历史任务卡。
输出：架构理解、当前可运行能力、验证命令与结果、缺失资产、可执行的下一步。
把记录保存到 outputs/handover_onboarding/，不要只留在聊天中。
```

## Prompt 02：每日只读巡检

```text
请按 docs/18_deployment_runbook.md 巡检 iSite2，加载适用 QA 控制。
检查 https://isite.cloud/health、runtime-config、UI、国家统计、一个物业详情、
中英文展示与一个已发布国家 Excel。检查容器、磁盘、内存/swap、OOM 和最近日志。
仅在已配置的授权连接上做只读检查；不发布、不扫网、不审批、不发邮件。
分别报告 API 存活、真实数据可用、报告可用和服务器资源，不把 health=ok 当成全部正常。
异常写明证据、影响、具体修复动作，保存 outputs/daily_health/<日期>/。
```

## Prompt 03：国家扫网计划，先不执行抓取

```text
请为 <国家> 的 <场景列表> 制定扩池计划，本轮合计期望新增 <数量> 个合格独立物业。
先读取现有 active/latest、overlay、KnownOpportunityIndex、已抓 URL 和缓存，
保存候选数/地图点数/城市覆盖/逐场景基线。
核验官方语言、实际搜索语言、主要渠道、本地域名、量化主指标术语，
保存 localization_profile.json 和 city × scene × metric 查询矩阵。
优先本地官方/监管/运营方/行业目录；说明扩大来源面和城市精搜两阶段。
只做计划与已存在资料复用，不执行新的 live scrape 或收费调用。
输出范围、脚本参数、请求预算、配额、停止条件、预计产物与可验证目标。
```

## Prompt 04：执行一轮受控扩池

```text
执行 <计划目录> 中已确认的 <国家>/<场景> 扩池任务。
允许公开网络查询与本地工作库更新；本轮最多 <搜索数> 次 search、<页面数> 次 fetch，
云 Firecrawl 预算为 0。需要 Firecrawl 时统一本地 wrapper，每条 agent 链同时最多 1 个 subprocess。
先查已知物业/URL、先 search 留 manifest，再抓少量高质量页面。
复用 docs/14_sweep_script_reuse.md 入口，候选清单外置 JSON，不写国家专属脚本。
必须验证真实物业、运营状态、物业级坐标、允许的量化主指标、来源/日期/Tier。
不足时依次扩大来源面与城市精搜，不能重复实体或降低门槛凑数。
完成入库、图片/重复门、城市规范化和最后同步后的完整后处理与 QA。
本轮不发布生产、不发邮件。保存 before/after、变更 property_id、来源/城市/场景分布、
实际净增、shortfall、费用、QA artifact、逐条补证动作和下一条命令。
```

## Prompt 05：已有物业补证与定向刷新

```text
请补查 <国家> 中 property_id=<UUID列表> 的 <缺失字段>。
先读现有证据和 hash，优先缓存、本地语言官方/运营方来源，保留原文、单位、期间、URL、日期、Tier。
补证通过现有 curation/overlay 流程写入，不直接改主表结论，不覆盖更强直接证据。
发现冲突写 Review Queue。仅对实际变更物业执行 derived、Traffic、本地化和预聚合检查。
不要 --force 全库刷新；不发布。输出字段前后值、来源、hash 变化、影响范围与 QA 结果。
```

## Prompt 06：主指标异常或数字污染修复

```text
请定位 <物业/UUID> 的 <异常指标>。先冻结当前 packet 和证据，核对原始单位、年份、期间、
单体/集团边界、实际/规划容量及 annual/daily/event 区别。
检查 metric_identity、metric_safety、Traffic 和 derived 路径，不仅修改 UI 文案。
若可确定复现，先补回归测试再修复，保留前后值和来源；不要用年份、属性编号或模型猜测补数字。
更新适用 QA 控制与事故归档，只刷新受影响物业并说明是否需要重新发布报告。
```

## Prompt 07：图片与重复物业检查

```text
检查 <国家/物业UUID范围> 的图片和重复记录，复用 run_apac_image_and_duplicate_gate.py，
显式传国家、db-url、source-date 和本轮输出目录，不使用旧 APAC 默认值。
先做不变更的审计：图片是否属于目标物业、是否破图/Logo/占位图/地图、是否图片代理 URL；
重复判断结合别名、URL、坐标、城市和 identity key，疑似项不要自动合并。
需要补图时先用物业官网/证据页。三次独立失败搜索必须留存审计，缺图保持明确状态。
给出具体修改后执行本地修复，最终同步完成后再做衍生、本地化和 QA；不发布生产。
```

## Prompt 08：生成国家报告

```text
请生成 <国家列表> 的 <中文/英文/双语> 标准国家报告，产物为 <Excel-only/Excel+PPT>。
读取 QA controls 与输出合同，确认 active/latest、canonical metric、结论、推荐总数和翻译缓存。
复用 generate_standard_country_report.py 与 templates/ppt/，复用指纹一致的已审计输出。
Excel 主表短硬，证据/模型/参数/方法/复核分表；全量报告不能被 UI Top N 截断。
执行脚本审计和实际渲染检查，保存预览，核查全名、图片、字号、溢出与统计逐格回算。
缺证据不能填 0 伪装为确认不存在；估算和事实分开。
本轮只生成与审计，不发邮件。返回产物路径、数据指纹、QA artifact 与未解决项。
```

## Prompt 09：发布前预检查

```text
为 <国家/物业UUID> 准备生产发布，先只做预检查和可审阅产物。
加载发布、城市、本地化、输出和运维 QA 控制，检查工作库来源、最新代码、变更 ID、
critical/high 门、cache miss/pending/error 全零、国家报告指纹与哈希。
选择最小影响范围：小更新用 property-scoped delta；全快照仅用于明确全量发布。
保留生产备份、发布 manifest、回滚命令与预计影响。
注意 country delta --dry-run 仍需 SSH 读取生产；snapshot --dry-run 可能写本地缓存/报告并调用模型。
输出具体发布命令与验收清单，不修改生产、不发邮件。
```

## Prompt 10：执行已审阅的数据发布

```text
现授权执行 <预检查目录/manifest> 对应的 <国家/物业UUID范围> 数据发布。
发布前确认预检查与当前数据/代码一致，确认备份和回滚文件存在。
使用交接手册既有发布脚本，禁止用 --allow-existing-target 进行未经计划的整国替换。
发布后验证 health、runtime-config、目标/非目标国家计数、物业详情、中英缓存、地图和报告下载。
比较报告 state hash/文件 hash，保留线上截图与 HTTP/数据结果。
失败时停在清楚状态并按对应版本回滚，不能连续重试扩大影响。
返回发布 ID、代码 SHA、实际变更、验证证据、回滚位置。不发邮件。
```

## Prompt 11：新机器部署网站

```text
请按 docs/18_deployment_runbook.md 在 <授权服务器> 部署 iSite2，域名为 <域名>，
目录为 <远端目录>，版本为 <Git SHA/标签>。这是新部署，不覆盖已有业务库。
先检查 DNS、端口、Docker、磁盘/内存和端口冲突；秘密值只从受控环境文件读取。
构建 web，启动 postgres，创建独立 ops 库，再启动 web/Caddy；保持 public_view。
用已审核交接数据完成首次快照发布，核验中英数据、地图、报告、登录和提单隔离。
保存部署记录、镜像/代码版本、备份位置、启动/停止/恢复步骤与实际验收，不凭容器 running 宣称成功。
```

## Prompt 12：查看和处理提单

```text
请读取 .secrets/service_requests.env（仅用于连接，禁止输出内容），
通过 service_request_admin.py digest 获取待处理摘要。
输出脱敏的精确编号、类型、范围、状态、预计执行动作；此任务不批准、不执行、不发邮件。
```

审批后另开明确任务：

```text
批准并执行提单 <精确编号>，批准范围为 <具体范围>。
先核对请求内容、合计目标和自定义国家/城市实体，按标准流程记录 approve/start。
完成后做 QA 与上线/报告验收，生成符合状态机的 execution_result 和 product_update。
<选择并保留一项：本轮只准备交付；或 已授权向该提单登记邮箱发送完成邮件>。
只有已授权发件时运行 complete；失败保持同一提单，用幂等机制处理。
```

## Prompt 13：开发功能或修复缺陷

```text
请实现/修复 <具体问题与期望行为>。先阅读相关代码、现有 diff、AGENTS 与适用 QA 控制。
找最小复现，复杂规则先测试后实现；复用现有模块，不新增一次性脚本。
如改字段，同步 Pydantic/JSON Schema/DB/API/前端/测试；外部 provider 先 interface 与 fake。
保留事实、推测、现网状态的边界。跑相关回归和前端构建；UI 改动补对应浏览器验证。
提交说明写清问题、最终行为、验证、限制和数据迁移影响。本轮不部署生产。
```

## Prompt 14：中断恢复与交班

```text
接续 <run目录/上一份交班记录>。先核对 checkpoint、active/overlay、manifest、最后成功物业、
证据 hash、发布 ID 和未完成 QA。只继续剩余步骤，不能从头重复抓取或 --force 全库重算。
若上下文与磁盘不一致，以可核验 artifact 为准并解释差异。
更新记录：目标、已完成、当前分支/SHA、数据库、失败原因、剩余动作、下一条命令、
是否已发布/发件、回滚位置。不要只靠聊天记忆。
```

## Prompt 15：迁移或重建定时任务

```text
请先盘点当前 iSite2 自动化，再为新负责人的项目重建 <任务名与运行时间/时区>。
保留原状态与授权范围；不要直接恢复所有历史暂停任务。
明确工作目录、个人登录态、数据库、最大请求/并发、checkpoint、QA 与发布/发件范围。
旧任务需由原负责人停止或移交，避免两台机器同时执行。
只在完成、失败、需要人工处理或状态发生有意义变化时通知。
重建后做一次有界验证，并把任务名、时区、状态、负责人和恢复方式写入交班记录。
```

## Prompt 16：GitHub 同步

```text
将本次已验证的 iSite2 代码和文档同步到现有私有仓库。
先查 git status/diff，确认用户已有修改归属；扫描待提交文件，排除数据库、证据缓存、
环境秘密、个人登录态、客户邮箱、输出包与构建目录。不要 git add . 盲目全加。
在明确文件清单上提交，先确认远端分支未前进，正常 push，禁止 force push。
给出 commit SHA、仓库链接、包含范围、测试结果。Git push 不触发或替代线上部署。
```
