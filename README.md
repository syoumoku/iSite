# iSite2

基于公开证据的高价值楼宇与室内覆盖机会扫描产品。包含多语言候选发现、字段级证据、分场景模型、独立现网状态、需求估算、世界地图、Excel/PPT 输出与独立运营提单。

## 接手从这里开始

**[完整交接文档](docs/16_handover.md)** 是人员和 AI agent 的统一入口。当前实现为 Python/FastAPI + React/Vite + PostgreSQL/SQLite，早期启动包 README 已存档到 `docs/00_starter_readme.md`。

| 文档 | 用途 |
| --- | --- |
| [交接总册](docs/16_handover.md) | 产品、架构、流程、数据口径、日常操作、代码索引 |
| [Codex 常用 Prompt](docs/17_codex_prompt_playbook.md) | 可复制的接手、扫网、补证、QA、发布、开发和交班任务 |
| [部署与恢复手册](docs/18_deployment_runbook.md) | 本地启动、Firecrawl、Docker 网站部署、发布、备份与回滚 |
| [交接清单与验收记录](docs/19_handover_inventory.md) | 本次验证、资产边界、权限和遗留问题 |

## 给接手 agent 的第一句话

```text
请先阅读 AGENTS.md、docs/16_handover.md、docs/19_handover_inventory.md、
.agents/skills/isite2-scan/SKILL.md、docs/14_sweep_script_reuse.md、
docs/15_qa_lessons_learned.md 和 config/qa_controls.yaml。
核对当前 Git、运行环境、数据来源和服务状态，再按交接文档完成本地启动验收。
不要把历史开发任务卡当作当前待办，不覆盖已有工作；缺少的数据或权限明确列出。
```

## 最小本地启动

需要 Python 3.12、Node.js 22 和 npm。从仓库根目录运行，以下使用新建演示库：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix ui/world_map ci
npm --prefix ui/world_map run build
mkdir -p outputs
export PYTHONPATH=src
export DATABASE_URL=sqlite+pysqlite:///outputs/isite2_demo.db
export ISITE2_OPS_DATABASE_URL=sqlite+pysqlite:///outputs/isite2_demo_ops.db
export ISITE2_ENABLE_OVERLAY_SYNC=0
export ISITE2_ENABLE_DERIVED_REFRESH_AFTER_SCAN=0
.venv/bin/python -m uvicorn isite2.api.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/ui/`，健康检查 `/health`。空演示库无业务点位属正常；恢复真实数据后按部署手册切换数据库。

代码、模板和配置进入 Git；数据库、证据缓存、真实环境文件与凭据单独交接。`AGENTS.md` 为工程约束，`config/qa_controls.yaml` 为 QA 控制索引。旧 `docs/01`–`docs/13`、`tasks/codex/` 中部分内容是设计历史，操作步骤以当前代码和交接册为准。
