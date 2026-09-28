# iSite2 交接清单与验收记录

核验日期：2026-09-28。本文件区分已经验证的事实、提供的资料、仍需负责人移交的资产。后续接手时更新状态，不能把本文当作未来长期健康保证。

## 1. 交付入口

- [交接总册](16_handover.md)：流程、产品、架构、数据口径与代码索引。
- [Prompt 手册](17_codex_prompt_playbook.md)：16 类日常操作任务。
- [部署与恢复](18_deployment_runbook.md)：命令、发布、备份、回滚、排障。
- [代码仓库](https://github.com/syoumoku/iSite)：私有，默认分支 `main`。本次交接提交及后续验证修复以 Git 历史和交付消息中的 SHA 为准。

## 2. 本次代码整理范围

交接前远端基线为 `3a84d33c71500b85340f4b85268e2dcbe5a2e208`。本机有大量已开发但未同步的功能，本次按文件清单归集到 Git：现有后端/前端、Traffic V2、网络信号、多语言缓存、城市规范化、提单、国家报告和 delta 发布、测试、配置、PPT 模板、交接资料。

为可移植性补充：

- 将原机器另一个目录中的 Firecrawl Compose、启动脚本、SearXNG 配置收录到 `deploy/firecrawl/`，真实环境文件排除；示例通过 Compose 服务名访问 SearXNG。
- `.env.example` 的 Firecrawl 地址改为项目规定的本地入口。
- `.gitignore` 和 `.dockerignore` 排除真实 env、数据库、缓存、构建、备份、业务输出和私有交接包。
- 旧启动包 README 存档到 `docs/00_starter_readme.md`，新 README 链接当前运行手册。
- cron 示例去除原负责人绝对路径，QA 复用指南的事故记录入口与当前控制规则统一。

代码上传不包含旧机器的所有文件，也不会自动部署生产网站。

## 3. 本次实际验证

可随仓库查阅的脱敏摘要：[验证记录 JSON](handover_validation/2026-09-28.json)。完整原始日志保留在原机 `outputs/handover_20260928/`，按需单独交接。

| 项目 | 结果 | 证据/范围 |
| --- | --- | --- |
| GitHub 仓库 | 已核验 private、main、可 push | GitHub repository metadata 与 SSH `ls-remote` |
| 后端测试 | **497 passed，2 warnings** | `outputs/handover_20260928/pytest.log`；隔离测试 DB，关闭 overlay 自动同步；耗时约 13 分 41 秒 |
| 前端构建 | **通过** | `outputs/handover_20260928/ui_build.log`；TypeScript + Vite，存在大 chunk 提示 |
| 浏览器关键回归 | **4 passed** | 默认地球、API 失败重试、全局别名搜索、public 隐藏写入口；desktop Chromium 单 worker |
| 线上只读 HTTP | **3 个 200** | `https://isite.cloud/health`、`/runtime-config`、`/ui/`；配置模式 `public_view` |
| 核心 SQLite 备份 | **integrity_check=ok** | SQLite backup API 生成一致性副本 |
| 生产/Firecrawl Compose | **配置解析通过** | 使用示例环境文件 `config -q`；未改动正在运行的服务 |
| Python 打包 | **wheel 构建通过** | `outputs/handover_20260928/package_build.log`；不等于已完成异机全依赖部署 |
| npm 依赖审计 | **存在未处理告警** | 7 项：1 critical、4 high、1 moderate、1 low；[完整报告](handover_validation/2026-09-28_npm_audit.json) |
| 全新 Docker 镜像 | **构建、隔离启动通过** | Node 22 构建 + Python 3.12；临时 SQLite 中 6 个 GET 返回 200，POST `/scan-runs` 返回预期 403；测试容器已清理 |
| QA 控制复查 | **4 passed** | 文档与事故归档整理后重新运行 `tests/test_qa_controls.py` |

本次没有替新负责人创建账号，没有新增云服务器或修改 DNS，没有发布/回滚生产业务数据，也没有给客户发邮件。4 项浏览器检查不是全量移动端验收；3 个线上 HTTP 200 不代表所有国家数据和报告已重新审计。Docker 验证采用一次性 SQLite；接手方完整生产 Compose/PostgreSQL、DNS/HTTPS、恢复切换及外部来源可用性仍须在其环境演练。

本机版本：Python venv 3.12.14、Node 24.15.0、npm 11.12.1、Docker Compose v2.40.2。Dockerfile 使用 Python 3.12 与 Node 22。记录版本是本次证据，不是“只有此版本可用”的承诺。

## 4. 已准备的核心数据包（单独传递）

原机位置：`.handover-private/isite2_core_data_20260928.tar.gz`，约 108 MB（十进制），不进入 Git。

```text
SHA-256: 3a850c73962bb15009020e198676177d368ebc794ca88a5eeb72b7efc5a77fe0
```

包含 SQLite 研究工作库一致性副本、overlay、intake/state（存在时）、包内 manifest 与逐文件哈希。不包含 ops 个人信息、账号凭据、Codex 登录态、完整文件缓存或历史报告。数据库含研究资料，按私有业务资产转交，不能公开分发。

本次只读库存，**以下是历史表行数，不是 UI 当前候选数，也不是全国市场总量**：

| 资产 | 状态/规模 | 交接动作 |
| --- | --- | --- |
| `outputs/isite2_dev.db` | 约 330 MB；40 表；properties 7608、scan_runs 21、scan_candidates 8324、evidence_items 9583、localized_text_cache 124923 | 已做核心包备份；恢复后按 latest 重新核对 |
| `outputs/regional_scan_loop/source_registry_overlay.yaml` | 约 18 MB | 核心包已包含；与 active 核对再同步 |
| `outputs/regional_scan_loop/state.json`、`intake_drafts.json` | 状态/草稿 | 存在的文件已随包保留 |
| `outputs/isite2_ops.db` | 本机存在，约 90 KB；4 表 | **未打入核心包**；生产 ops 可能是另一数据库，须单独核验与转交 |
| `.web_evidence/` | 本机约 2.4 GB | 按需完整迁移，保留相对路径和合规记录 |
| `.firecrawl/` | 历史缓存 | 不在核心包，需另行盘点 |
| `outputs/` | 本机约 20 GB，含大量历史产物/备份 | 核心包只含指定文件；选择迁移 QA/release/report/模型缓存 |
| `data/isite2.db` | 较旧数据；另有两个空 DB | 不当作主交接库，不覆盖新工作库 |
| `templates/ppt/` | 中文/英文模板与参考图 | 已纳入 Git |
| 生产 `public_reports/` 与 release manifests | 服务器资产 | 连同生产展示库备份移交，不能只复制网页源码 |

如交接期间仍有采集写入，核心包之后的变化需要再做一次截止时间明确的补充备份；本包不是持续同步。

## 5. 权限与秘密交接表

以下不在代码仓库中填真实值，只记录密码管理器/受控渠道引用与签收状态：

| 项目 | 原负责人需要交付 | 接手验收 |
| --- | --- | --- |
| GitHub | collaborator 权限或明确的仓库所有权转移安排 | 本人账号 clone/push；仓库继续 private |
| 服务器 | 云平台归属、SSH 账户/公钥授权、目录、Docker 权限 | 用本人身份登录；确认运行版本和服务 |
| 域名/DNS | `isite.cloud` 注册商、DNS、续费归属 | 能查看记录与到期时间，不先切 DNS |
| 网站 env | `deploy/public.env` 受控移交 | 新密码/secret、数据库、ops、feature flags 一一核验 |
| 发布连接 | SSH target、远端目录、部署用户、host key | 一次只读远端检查 |
| 模型 | 新负责人自有 Codex/API 账户与预算 | provider smoke；不移交个人 OAuth token |
| 邮件/提单 | SMTP 账号与授权码、admin token、API base | 先查摘要；测试发送仅限另行指定收件人 |
| 第三方 | Placer/地图/搜索/可选数据的权限与条款 | 核对功能开关与调用范围 |
| 数据 | 核心包、完整缓存、ops、报告、历史 release/备份 | 校验 hash、完整性、latest、恢复演练 |
| 备份 | 异机备份位置、保留周期、加密密钥归属 | 至少一次恢复成功 |

尚未指定接手人的账号，无法替其签收权限。不要把本机 `.secrets/` 或 `~/.codex/auth.json` 推到 Git 解决权限问题。

## 6. 原机器 Codex 自动化库存

这是本次只读核对的状态；没有创建、修改、启用或停用任务。时间按原机 Asia/Shanghai 解读，迁移时需在新界面再次核对。

| 名称 | 状态 | 计划 | 迁移注意 |
| --- | --- | --- | --- |
| iSite2 daily public UI and data sync | PAUSED | 每日 02:30 | 不默认恢复；先确认发布范围/备份 |
| iSite2 daily service request approval digest | ACTIVE | 每日 09:00 | 先查实际 prompt 的通知/SMTP 行为；切人时避免双跑 |
| Check localization repair | PAUSED | 每 30 分钟 | 历史任务跟进，不直接作为新常驻任务 |
| 自动扫网 | PAUSED | 每小时 | 需明确国家/预算/并发再重建 |
| iSite2 new-evidence regional daemon supervisor | PAUSED | 每小时 | 核对 daemon/state/工作目录后才启用 |

自动化配置在 Codex 用户目录，不随 clone/data 包迁移。旧工作目录绑定原机器；不能复制路径后声称新机定时任务已运行。通知、审批、发布、邮件权限分别核对。

## 7. 已知限制与优先待办

| 优先级 | 问题/限制 | 下一步 |
| --- | --- | --- |
| P0 接手必做 | 凭据、服务器、DNS、ops 与自动化仍需新负责人签收 | 按第 5/6 节逐项核验并记录负责人 |
| P0 发布前 | snapshot 正常结束删除 previous 库 | 独立 dump + 报告备份，再发布；做恢复演练 |
| P0 下一次生产代码发布前 | npm audit 有 1 critical / 4 high 等 7 项告警，MapLibre 为 critical | 评估 advisory 与实际暴露；报告建议 MapLibre 6.11.2（主版本升级），在隔离分支升级、构建并验证地球/卫星/交互兼容；不要直接 audit fix --force |
| P0 接手必做 | 本地 DB、overlay、线上展示库、ops 是不同资产 | 对齐截止时间和数据来源，按 latest 核对 |
| P1 | 部分脚本仍有历史默认国家/日期/SQLite 路径 | 显式传参数；先读 --help，不扩大旧脚本硬编码 |
| P1 | Alembic 配置默认内存库，运行时存在兼容建表 | 单独设计/演练迁移，不裸跑 upgrade |
| P1 | OpenAPI 静态稿与旧架构文档可能滞后 | 接口以当前代码/本地 openapi 为准；开发时同步 |
| P1 | Python 依赖为范围约束，Firecrawl/Caddy 等部分镜像 tag 可变 | 在稳定环境留版本与镜像 digest；未来建立依赖锁定 |
| P1 | 页面登录不是所有 GET 数据鉴权 | 如要整站私密，单独实现/配置边界鉴权 |
| P1 | 全量历史抓取缓存/报告未进入核心包 | 评估保留策略并异机迁移所需目录 |
| P2 | Vite 存在大 chunk 提示；FastAPI startup API 有弃用提示 | 在后续性能/维护任务中处理，不等于当前测试失败 |
| P2 | 磁盘 guard 包含原项目具体容器名 | 新机审阅适配后启用，不盲复制系统级配置 |

## 8. 签收验收单

- [ ] 新负责人 GitHub 权限验证，并记录实际交接 Git SHA。
- [ ] 新机器 clone、安装、独立空库启动、前后端测试通过。
- [ ] 核心包 hash/SQLite 完整性、latest 与 overlay 对账完成。
- [ ] 证据来源、推测、现网状态、Review Queue 能从任一物业追溯。
- [ ] 一个国家的中英报告生成、渲染 QA、指纹验证通过。
- [ ] 测试环境部署、delta 或 snapshot 发布、恢复演练通过。
- [ ] 生产代码、展示数据、报告、ops 的版本与备份对应。
- [ ] SMTP/提单权限与幂等状态验证，未误发客户邮件。
- [ ] 原机/新机自动化切换，确认只有一个有效执行方。
- [ ] 交接双方姓名、日期、资产引用、未完成事项与负责人记录在受控交班记录。

未勾选项不会因为代码已上传而自动完成。最终签收记录建议放受控运维空间，Git 仅保存脱敏版本。
