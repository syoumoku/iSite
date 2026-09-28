# iSite2 部署、日常命令、备份与恢复

核验日期：2026-09-28。代码来源：[私有 GitHub 仓库](https://github.com/syoumoku/iSite)。运行前阅读 [交接总册](16_handover.md) 与 `AGENTS.md`。本手册包含会修改数据库/发布的命令，只在对应任务已授权、目标已核对、备份完成时执行。

## 1. 环境与端口

| 项目 | 需要 |
| --- | --- |
| 本地工作站 | Python 3.12（项目声明 ≥3.11）、Node 22、npm、Git、Docker Compose v2 |
| 生产服务器 | Linux、Docker/Compose、Git、SSH、Python 3（发布激活脚本使用）、公网 DNS |
| 模型分析 | 接手人的 Codex CLI/登录态；或明确选用 `gpt` provider 与个人 API key |
| 本地 API / UI | `127.0.0.1:8000`，浏览器 `/ui/` |
| Playwright | `127.0.0.1:8001`；配置会复用现有服务，测试前检查端口占用 |
| 本地 Firecrawl | `127.0.0.1:3002/v1`；SearXNG `127.0.0.1:8080` |
| 生产公网 | Caddy 80/443；web 8000、数据库 5432 仅 Compose 内网 |

建议网站服务器从 2 vCPU/4 GiB RAM 和足够磁盘开始，再按实测调整；这不是容量保证。Firecrawl 的独立 Compose 含多个有内存上限的服务，建议使用另一台工作站/采集机并预留更大内存。前端构建峰值、数据库备份、快照 stage、报告和缓存都需要额外空间；不能只按源代码体积估算。

## 2. 新机器 clone 与本地启动

```bash
git clone https://github.com/syoumoku/iSite.git
cd iSite
git rev-parse HEAD
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix ui/world_map ci
npm --prefix ui/world_map run build
mkdir -p outputs .tmp
```

私有仓库需要接手人自己的 GitHub 登录或 SSH 权限。原机器使用的 `github.com-isite` 是个人 SSH 别名，不可直接复制为新机器地址。

采用演示库启动，不会加载历史业务数据：

```bash
export PYTHONPATH=src
export DATABASE_URL=sqlite+pysqlite:///outputs/isite2_demo.db
export ISITE2_OPS_DATABASE_URL=sqlite+pysqlite:///outputs/isite2_demo_ops.db
export ISITE2_APP_MODE=local
export ISITE2_ENABLE_OVERLAY_SYNC=0
export ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=0
.venv/bin/python -m uvicorn isite2.api.main:app --host 127.0.0.1 --port 8000
```

另一终端检查：

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/runtime-config
curl --fail http://127.0.0.1:8000/map/country-summary
```

打开 `http://127.0.0.1:8000/ui/`。空库无地图点位是正常现象。`/health` 仅表示进程响应，不能替代真实数据库查询。当前 Vite 配置没有 API 开发代理，最稳妥是构建后由 FastAPI 同源访问；不要把直接访问 Vite 开发端口报 API 错误当成后端不可用。

项目大量配置使用 `os.getenv`，并非所有脚本自动读取 `.env`。复制 `.env.example` 后，需要在 shell 导出变量或显式传参数。下列方式仅适用于自己受控、语法正确的 shell 环境文件：

```bash
set -a
. ./.env
set +a
```

`.env.example` 默认是本地 PostgreSQL；若使用交接 SQLite，务必覆盖 `DATABASE_URL`。不要因为 Postgres 连接失败触发 SQLite fallback 而误认自己连接到了生产/指定库。

### 使用本地 PostgreSQL（可选）

```bash
docker compose up -d postgres redis
export DATABASE_URL=postgresql+psycopg://isite:isite@127.0.0.1:5432/isite2
```

根目录 `docker-compose.yml` 是开发配置，含演示密码与映射端口，不能用于公网生产。Redis 是开发预留服务，并不代表所有扫网任务已自动进入队列。

## 3. 恢复交接业务数据

本次核心数据包保存在原机器 `.handover-private/isite2_core_data_20260928.tar.gz`，配套 `.sha256`；通过受控文件渠道转交，不上传 Git。包内是 SQLite 一致性备份、overlay、intake/state（存在时）和 `manifest.json`；不含账号、ops 库、完整页面缓存、历史报告。

在**新的 clone** 中，先停止本地写入进程，再执行：

```bash
(cd /受控目录 && shasum -a 256 -c isite2_core_data_20260928.tar.gz.sha256)
tar -tzf /受控目录/isite2_core_data_20260928.tar.gz
mkdir -p .handover-private
tar -xzf /受控目录/isite2_core_data_20260928.tar.gz -C .handover-private
```

将 `/受控目录` 替换为实际数据包目录。校验包内每个文件的 SHA-256 与 manifest 一致，再复制 `outputs/` 内容到新 clone 的 `outputs/`；如目标文件存在，先备份，不直接覆盖。

```bash
export DATABASE_URL=sqlite+pysqlite:///outputs/isite2_dev.db
export ISITE2_OPS_DATABASE_URL=sqlite+pysqlite:///outputs/isite2_ops.db
export ISITE2_ENABLE_OVERLAY_SYNC=0
export ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=0
```

先禁用自动同步/模型，核对数据库和 overlay 的国家/场景/物业差异。读 SQLite 完整性：

```bash
.venv/bin/python - <<'PY'
import sqlite3
with sqlite3.connect('file:outputs/isite2_dev.db?mode=ro', uri=True) as connection:
    print(connection.execute('PRAGMA integrity_check').fetchone()[0])
PY
```

核对通过后，研究任务再明确启用所需 hook：

```bash
export ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=1
export ISITE2_DERIVED_REFRESH_PROVIDER=codex-oauth
```

是否开启 `ISITE2_ENABLE_OVERLAY_SYNC=1` 由本轮 overlay 状态决定；API 启动同步可能触发真实入库及分析。不要仅为了页面出现数据而盲目开启。

完整延续历史抓取还需另行迁移 `.web_evidence/`、`.firecrawl/`、`outputs/derived_refresh/` 及有关 QA/release/report 目录；迁移时保留原相对路径。ops 数据另行加密备份恢复，接手新空 ops 库不会保留已有提单。

## 4. 本地 Firecrawl 与模型通道

### Firecrawl

本仓库 `deploy/firecrawl/` 已收录原机器运行配置的可移植副本。真实 `.env` 不入 Git；示例默认通过同一 Docker 网络的 `searxng` 服务连接，避免依赖另一目录或宿主机服务。

```bash
cp deploy/firecrawl/.env.example deploy/firecrawl/.env
chmod 600 deploy/firecrawl/.env
```

填写新的 `POSTGRES_PASSWORD`、`BULL_AUTH_KEY`、`SEARXNG_SECRET`；例如用 `openssl rand -hex 32` 生成后保存在受控文件中。保持 `SEARXNG_ENDPOINT=http://searxng:8080`。不要复用示例 secret。镜像 `latest` 可变，稳定环境应记录并固定各镜像 digest；交接清单记录了验证范围。

```bash
docker compose --env-file deploy/firecrawl/.env -f deploy/firecrawl/docker-compose.yml --profile container-searxng config -q
docker compose --env-file deploy/firecrawl/.env -f deploy/firecrawl/docker-compose.yml --profile container-searxng up -d
docker compose --env-file deploy/firecrawl/.env -f deploy/firecrawl/docker-compose.yml --profile container-searxng ps
export FIRECRAWL_BASE_URL=http://127.0.0.1:3002/v1
PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py --print-env
```

不要在已运行同名 `isite-firecrawl` 栈的旧机器直接启动副本。新机器无冲突且获准公开查询后，可做一次留盘 smoke：

```bash
mkdir -p .web_evidence/smoke
PYTHONPATH=src .venv/bin/python scripts/run_firecrawl_cli.py -- search "Kenya airport annual passenger official" --limit 1 --json -o .web_evidence/smoke/search.json
```

正常自动化不裸跑 `firecrawl`，不传 `--deployment cloud`；本地 wrapper 直接发 HTTP，本地模式不需要云 CLI 凭据。默认清理继承代理；确需代理才设 `FIRECRAWL_TRUST_ENV_PROXY=1`。空结果、服务故障和被站点阻挡是三种不同结果，都要保留 manifest。

Scrapling 可选依赖：`.venv/bin/python -m pip install -e '.[dev,sweep]'`。只用于 robots 允许的已筛 URL；遇登录、验证码、付费墙停止并进入 Review Queue，不绕过。

### 模型

运行 `codex --version`、`codex login`，使用接手人自己的账户。当前 OAuth adapter 会查找 `~/.codex/auth.json`，并启动 `codex exec`；CLI 不在 PATH 时设置 `ISITE2_CODEX_BIN`。若新版登录仅存系统钥匙串而 adapter 判定无 auth.json，记录兼容问题并修 adapter，不复制旧 token。

明确选择 API 通道时，私下配置 `OPENAI_API_KEY` 并对衍生脚本传 `--provider gpt`；本地化脚本的可用 provider 不同，先 `--help`，不能照搬选项。模型名和额度可变，不在文档锁死；记录实际 provider/model 和计费归属。

## 5. 常用操作命令

所有命令前先读 QA controls。国家名使用库中的 canonical 值；大多数命令有真实网络/数据库副作用。先 `--help` 核验参数。

| 操作 | 标准入口与注意点 |
| --- | --- |
| 只做补证计划 | `run_structured_first_backfill.py --countries "Kenya" --scenes airport_terminal,stadium`；国家/场景逗号分隔；固定读 `outputs/isite2_dev.db` |
| 一轮区域扫描 | `run_regional_loop.py --countries Kenya --max-searches-per-cycle 5 --max-fetches-per-cycle 10`；实际执行，不是 dry-run |
| 结构化导入 | `run_apac_other_scene_agent_import.py --input-files <JSON> --db-url <URL> --region <区域> --source-type <来源标签> --source-date <YYYY-MM-DD>` |
| 图片/重复门 | `run_apac_image_and_duplicate_gate.py --countries Kenya --db-url <URL> --source-date <YYYY-MM-DD> --output-dir <本轮目录>` |
| 城市审计 | `run_city_normalization_refresh.py --country Kenya --database-url <URL>`；默认审计，apply 参数另看帮助 |
| 衍生更新 | `run_gpt_derived_info_refresh.py --property-id <UUID> --provider codex-oauth --concurrency 1`；无 `--country` |
| Traffic | `run_traffic_v2_refresh.py --country Kenya --database-url <URL>` |
| 翻译预热 | `run_localization_refresh.py --country Kenya --locale zh --locale en --provider codex-oauth` |
| 报告 | `generate_standard_country_report.py --country Kenya --artifact-mode excel-only --max-workers 1`；完整包改 `--artifact-mode all` |
| 运营摘要 | `service_request_admin.py --api-base https://isite.cloud digest`；需秘密文件，不会批准 |

统一前缀：`PYTHONPATH=src .venv/bin/python scripts/`。上述表中的占位符须替换。

导入/图片脚本保留历史名称和旧默认国家/日期，必须显式传 `--countries`（适用时）、`--source-date`、`--db-url`。导入输入合同以脚本 `_load_candidates/_draft` 与 `tests/test_agent_scene_candidate_import.py` 为例，必须含 property/country/city/scene、坐标、objective metric 与来源等字段。不要把报告自然语言当作输入 JSON。

有些 `--dry-run` 会初始化本地 schema 或写审计文件；不是绝对无写入。发布 dry-run 见后文。报告默认 GPT audit 可能用模型；`--gpt-audit-provider off` 仅供明确的离线演练，不能省掉对外交付人工检查。

## 6. 新服务器部署网站

### 6.1 准备

将域名 A/AAAA 指向服务器，确认 80/443 能入站，SSH 仅授权人员使用。服务器无需公开数据库和采集服务端口。配置 HTTPS 时不要保留指向旧主机的错误 AAAA。

下面在服务器上运行，假定已创建并授权 `/opt/isite2` 目录：

```bash
git clone https://github.com/syoumoku/iSite.git /opt/isite2
cd /opt/isite2
git rev-parse HEAD
mkdir -p public_reports backups
chmod 700 backups
cp deploy/public.env.example deploy/public.env
chmod 600 deploy/public.env
```

编辑 `deploy/public.env`：域名、随机数据库密码、独立 ops 库名、访客账户新密码、`ISITE2_APP_AUTH_SECRET`、`ISITE2_REQUEST_ADMIN_TOKEN`。数据库密码建议 hex，避免 DSN URL 转义问题。示例 `visitor123456` 必须换掉；不能仅修改示例文件却让 Compose 继续读旧真实文件。

`ISITE2_PUBLIC_OOKLA_ENABLED=0` 为默认，授权/产品用途未审阅前不打开。Placer key 只放服务器，缺 key 时应显示不可用状态，不能伪造网络/人流图层。

### 6.2 构建、创建独立 ops 库、启动

为减少重复输入，在当前 shell 定义函数（每个新 shell 重新定义）：

```bash
dc() { docker compose --env-file deploy/public.env -f docker-compose.prod.yml "$@"; }
dc config -q
dc build web
dc up -d postgres
dc ps
```

等待 postgres 显示 healthy，再创建 ops 数据库。镜像不复制 `scripts/`，因此从宿主机将既有脚本送入一次性 web 容器：

```bash
dc run --rm --no-deps -T web python - < scripts/ensure_ops_database.py
dc up -d web caddy
dc logs --tail 80 web caddy
```

FastAPI repository 初始化模型表及兼容字段，ops 模块初始化独立表。**不要直接运行裸 `alembic upgrade head`**：现有 `alembic.ini` 默认内存 SQLite，且运行时已有兼容建表逻辑。需要手工迁移时先备份，在隔离库检查版本链，显式给 Alembic 绑定目标 URL，再选择受控迁移方案；不能对已建库盲跑 initial migration。

```bash
curl --fail https://你的域名/health
curl --fail https://你的域名/runtime-config
curl --fail https://你的域名/map/country-summary
```

应为 `public_view`，不开放扫描/连接器写入口。此时可能是空展示库；首次业务数据发布按第 7 节执行。Caddy 持久化卷保存证书；不要执行 `docker compose down -v` 删除数据库/证书卷。

## 7. 工作站向网站发布数据

先加载 `config/qa_controls.yaml` 与 `docs/15_qa_lessons_learned.md`。确认最后同步后衍生/Traffic/翻译完整，cache miss/pending/error 全零，城市规范化通过，国家报告可激活，磁盘低于脚本 85% 阻断线，并保存生产备份。

在工作站私有环境中设置：

```bash
export DATABASE_URL=sqlite+pysqlite:///outputs/isite2_dev.db
export ISITE2_PUBLIC_SSH_TARGET=deploy@你的服务器
export ISITE2_PUBLIC_REMOTE_DIR=/opt/isite2
export ISITE2_PUBLIC_REMOTE_ENV_FILE=deploy/public.env
mkdir -p .tmp outputs
```

SSH 用户需能在远端目录执行 Docker，工作站需有 `ssh/scp`。若源是 PostgreSQL，全快照还需要与服务器兼容的 `pg_dump`；country delta 当前只接受 SQLite 源。

### 首次 / 明确的全量发布

```bash
PYTHONPATH=src .venv/bin/python scripts/publish_public_snapshot.py --dry-run
PYTHONPATH=src .venv/bin/python scripts/publish_public_snapshot.py
```

第一条会构建本地快照、预聚合及国家报告，可能刷新翻译并调用模型；只是“不远端恢复”，不是无副作用。检查 `outputs/public_snapshots/<release>/manifest.json` 和产物后才执行第二条。每次运行重新生成 release，不是执行上一份 dry-run 文件。

全快照会建 stage 库、校验计数、停止 web、换库后启动，并在正常流程末尾**删除 previous 库**。因此 previous 不是持久回滚保障；一定要有独立 `pg_dump` 备份及报告目录备份。

### 新国家或少量物业更新

新国家：

```bash
PYTHONPATH=src .venv/bin/python scripts/publish_public_country_delta.py --country Kenya --dry-run
PYTHONPATH=src .venv/bin/python scripts/publish_public_country_delta.py --country Kenya
```

已有国家的小更新：在以上命令添加一个或多个 `--property-id <真实UUID>`。已有整国默认拒绝替换；不要为图省事加 `--allow-existing-target`，该模式回滚语义不等于恢复旧整国数据。

delta 的 `--dry-run` **仍需 SSH 且会只读检查远端**，并生成本地 SQL/报告/manifest；不是离线模式。结果在 `outputs/public_deltas/<release>/`，包含 rollback SQL；物业模式在远端备份目标旧行。发布后必须核对非目标国家未变。

### 发布后验收

- `/health`、`/runtime-config`、`/map/country-summary` 与目标物业详情正常。
- latest 候选、Map Ready、城市和场景统计分别与基线对账，不能用历史表总行数。
- 中英文没有 Localization pending、错误方向文本或数字变化。
- `/outputs/excel/country?country=Kenya&locale=en` 返回正确文件；报告索引、state hash、文件 SHA-256 对应当前数据。
- 真实浏览器检查国家选择、地图标记、物业详情、图像、筛选、登录与报告下载；保存截图。
- 保存 release ID、Git SHA、manifest、目标 ID、QA、备份与回滚路径。生产数据核验完成才推进提单交付。

## 8. 仅更新网站代码

备份完成，在服务器记录旧 SHA，检查工作区干净，然后：

```bash
git fetch origin
git pull --ff-only origin main
dc build web
dc up -d --no-deps web
```

Caddy 或环境文件也改动时按实际变更重建对应服务。代码更新不会自动刷新业务数据；数据库字段变化需先演练兼容迁移。回滚代码应检出记录的旧 SHA、重建 web；若已经变更 DB，必须同时评估数据版本，不能假设回滚镜像就恢复一切。

## 9. 备份与恢复

### 本地 SQLite 一致性备份

不要在有写入/WAL 时只复制 `.db`。使用 SQLite backup：

```bash
mkdir -p .backups
.venv/bin/python - <<'PY'
import sqlite3
from datetime import datetime, timezone
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
source = sqlite3.connect('file:outputs/isite2_dev.db?mode=ro', uri=True)
target = sqlite3.connect(f'.backups/isite2_dev_{stamp}.db')
source.backup(target)
print(target.execute('PRAGMA integrity_check').fetchone()[0])
target.close()
source.close()
PY
```

overlay/state 与数据库应在同一暂停写入窗口打包并记录 hash。ops 库单独备份，按含个人信息的运营数据控制访问。

### 生产备份

服务器中，`dc` 为第 6 节函数；若更改了默认 DB 用户/库名，下列参数同步替换。发布前暂停相关写入以保证 ops 和文件状态一致：

```bash
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
dc exec -T postgres pg_dump -U isite -d isite2_public -Fc > "backups/public_${backup_stamp}.dump"
dc exec -T postgres pg_dump -U isite -d isite2_ops -Fc > "backups/ops_${backup_stamp}.dump"
tar -czf "backups/public_reports_${backup_stamp}.tgz" public_reports
git rev-parse HEAD > "backups/code_${backup_stamp}.txt"
```

核对退出码、文件大小、`pg_restore --list`，记录 SHA-256；将备份加密保存到另一台机器。真实 env、DNS、证书/卷恢复信息另存密码管理器/运维系统。不是生成一个本地 dump 就算异机备份完成。

### 恢复演练（默认使用新库，不覆盖线上）

```bash
dc exec -T postgres createdb -U isite isite2_restore_check
dc exec -T postgres pg_restore -U isite -d isite2_restore_check --no-owner --exit-on-error < backups/选定版本.dump
```

新库名必须不存在。恢复后核对表/查询/报告指纹；切换生产前停止 web，保留当前故障库，按已验证方案替换目标库并恢复同版本 `public_reports`，然后启动验收。ops 库和展示库不能互相覆盖。

delta 回滚：使用**同一 release** 生成的 rollback SQL，对照 manifest 确认范围和远端旧行备份；先在隔离库验证，再维护窗口执行。数据回滚后报告索引也需恢复到匹配版本。整国替换不能依赖“删除新增行”的 rollback 恢复旧数据，应使用发布前 dump。

## 10. 测试与诊断

隔离测试示例：

```bash
mkdir -p .tmp/test outputs
export PYTHONPATH=src
export DATABASE_URL=sqlite+pysqlite:///.tmp/test/default.db
export ISITE2_OPS_DATABASE_URL=sqlite+pysqlite:///.tmp/test/ops.db
export ISITE2_ENABLE_OVERLAY_SYNC=0
.venv/bin/python -m pytest -q
npm --prefix ui/world_map run build
npm --prefix ui/world_map run smoke -- --project=desktop --workers=1
```

浏览器需安装 Playwright Chromium：在 `ui/world_map` 中运行 `npx playwright install chromium`。测试配置使用 8001 并可能复用旧进程；不要对不明旧服务测试。本次验证基线见 [验收记录](19_handover_inventory.md)。

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| 空地图 | 实际 DSN、latest 数、Map Ready、API 错误 | 区分空数据、坐标门与网络失败，不造点 |
| 本地有/线上无 | 发布 manifest、目标 ID、数据指纹 | 本地保存不等于已发布 |
| `Localization pending` | 翻译缓存 hash/locale/有效性 | 定向刷新，再预聚合；GET 不现场翻译 |
| Firecrawl 连接失败 | Docker ps、3002、wrapper print-env | 恢复本地服务，保留失败，不切云绕过 |
| OAuth unavailable | codex 路径、本人登录、adapter auth 检查 | 修登录/兼容，不复制他人凭据 |
| country delta 拒绝已有国家 | 范围是否本应是物业更新 | 传明确 property ID，不绕整国保护 |
| 报告 not ready / stale | index、state hash、审计、挂载目录 | 从同一 payload 重建审计并激活 |
| 502 / 证书失败 | dc ps/logs、DNS A/AAAA、80/443 | 修服务/DNS，不重复覆盖库 |
| 数据库不存在 | 是否创建独立 ops、DSN 是否误指 | 执行 ensure_ops_database 或更正配置 |
| 大量缓存/磁盘 ≥85% | df、docker system df、日志、备份 | 暂停重任务，清有保留策略的旧产物，不删卷 |

`deploy/docker-json-log-guard` 和 systemd timer 是可选运维模板，内含具体容器名与清理动作；先审阅并按新机器容器名适配，再启用。生产 Compose 已设置日志轮转，不能直接覆盖整机 Docker daemon/journald 配置而影响其他项目。

## 11. 自动化与交班

定时任务存于原负责人 Codex 主机，不在 Git。逐项核对当前状态、时区、工作目录、模型登录、数据路径、发布与发件权限。默认沿用暂停状态；只重建业务确认需要的任务。旧负责人停用后新负责人再启用，首轮有界验证。详细 prompt 见 [操作手册](17_codex_prompt_playbook.md)。

每次交班写明：Git SHA、实际数据库、最近 run/release、已完成步骤、剩余 QA、已发邮件与否、失败 checkpoint、下一条命令、回滚路径。不要依赖上一位使用者的聊天历史。
