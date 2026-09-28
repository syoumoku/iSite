# 2026-09-28 交接构建依赖审计

## 发现与影响边界

为交接验证新 Docker 构建时，`npm ci` 报告 7 项依赖告警。随后在同一锁文件上使用 HTTPS 官方 registry 运行 `npm audit --json`，结果为 1 critical、4 high、1 moderate、1 low。功能构建和回归测试通过不消除这些告警。

critical 包为直接依赖 `maplibre-gl`。报告给出的修复版本为 6.11.2，标记 `isSemVerMajor=true`。实际影响须结合 [advisory](https://github.com/advisories/GHSA-jrc7-96c5-q579)、本项目输入路径和部署方式评估；本次未声称已验证可利用性，也未进行攻击验证。

high 包包括 browserslist、nanoid、postcss、vite；详见结构化 artifact。开发依赖与浏览器运行依赖的暴露不能混为一谈。

## Artifact 与控制映射

- [完整 npm audit JSON](../handover_validation/2026-09-28_npm_audit.json)。
- [交接验证摘要](../handover_validation/2026-09-28.json)。
- 映射 `QA-OPS-001`：本次交接运维人工 gate 留存依赖审计与待办；未创建一个只有自然语言的新自动控制。
- [交接清单](../19_handover_inventory.md) 将此列为下一次生产代码发布前优先处理项。

## 后续执行动作

1. 保存当前 lockfile/镜像版本，阅读报告所附 advisory 并界定受影响路径。
2. 单独升级受影响依赖；MapLibre 主版本变更需检查 API、样式与地图事件兼容。
3. 执行 TypeScript/Vite build、地图/筛选/卫星/hover/移动端相关 Playwright；如改业务逻辑，执行对应后端回归。
4. 重跑 npm audit，保留前后报告；残余项明确说明与处理期限。
5. 通过后再发布生产代码。不要用 `npm audit fix --force` 作为交接时的盲目处理。

本次仅上传交接代码和资料，没有将新构建部署到生产，没有改动 lockfile 进行主版本升级。
