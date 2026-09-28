# iSite2 Codex Starter Pack

面向 Codex 开发的 iSite2 多 agent 扫网产品启动包。目标是把上传的 iSite Skill 转成一套可开发、可测试、可扩展的工程骨架，而不是只停留在报告模板。

## 一句话产品定义

iSite2 是一个面向高价值楼宇室分机会识别的自动化 AI 产品：后台按国家/区域/城市滚动扫描公开证据，建立高价值楼宇候选池，按场景模型提取主指标与证据链，独立判断现网建设状态，计算需求链路，输出 Excel、PPT 洞察卡和世界地图 UI。

## Codex 开发入口

1. 把整个 `isite2_codex_starter_pack` 放进新 repo 根目录。
2. 启动 Codex 前先让它读取：
   - `AGENTS.md`
   - `.agents/skills/isite2-scan/SKILL.md`
   - `docs/02_architecture.md`
   - `tasks/codex/00_bootstrap_foundation.md`
3. 在 Codex 里可以直接用这个首条任务：

```text
请读取 AGENTS.md、.agents/skills/isite2-scan/SKILL.md 和 tasks/codex/00_bootstrap_foundation.md，按 iSite2 starter pack 实现第一阶段 MVP。保持证据优先、分场景建模、现网状态独立判断、推测留痕和 Review Queue 规则。
```

## 推荐技术栈

- Backend: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy, Alembic
- Queue: Celery/RQ + Redis；后续可升级 Temporal
- DB: PostgreSQL + PostGIS
- Crawler/Search connectors: 官方 API 优先；无 API 时使用受控网页抓取，必须遵守 robots.txt、限速、来源记录
- Output: openpyxl / python-pptx
- Frontend: Next.js + React + Mapbox/Leaflet
- Agent runtime: 先用规则化 pipeline + LLM task adapter，后续再接 OpenAI Agents/Responses API 或 MCP 工具

## 本包内容

```text
AGENTS.md                         Codex 全局开发约束
.agents/skills/isite2-scan/        Codex skill，触发 iSite2 工作流
.codex/agents/                    Codex 自定义子 agent 配置
docs/                             产品、架构、UI、合规、增长闭环说明
docs/14_sweep_script_reuse.md     扫网脚本复用分层、标准动作链和 legacy 入口规则
config/                           agent、场景、阶段门、输出模板配置
schemas/                          JSON Schema 数据契约
db/                               PostgreSQL/PostGIS schema 与种子数据
api/openapi.yaml                  API 草案
src/isite2/                       Python 后端最小代码骨架
tests/                            合同与规则测试样例
tasks/codex/                      让 Codex 分阶段开发的任务卡
prompts/                          产品内 agent 提示词模板
scripts/                          本地校验和模板生成脚本
```

## MVP 验收口径

第一阶段只追求“跑通闭环”，不要急着把全球扫完：

- 能创建一个国家级 scan run。
- 能写入候选物业、证据、推测、现网状态、需求计算、结论、复核队列。
- 能输出符合固定表头的 Excel skeleton。
- 能返回地图点位 GeoJSON。
- 能打开 `/ui/` 查看世界地图、筛选 KPI、点位详情 drawer、证据/推测/复核 tab。
- 所有推测能追溯，所有 Review Queue 有具体下一步动作。

## 非目标

- 不做工程级室分设计图。
- 不做精确预算。
- 不替代现场勘察。
- 不进行网络端口扫描或任何入侵式探测；“扫网”仅指公开网页/公开数据证据扫描。
