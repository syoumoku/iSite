# 自动成长与迭代机制

## 增长来源

1. 新国家/城市扫描结果。
2. Review Queue 人工复核反馈。
3. 来源可信度反馈。
4. 推测结果被证实/证伪。
5. 现网建设公告更新。
6. 场景规则调整。

## 版本化对象

- scene_models.version
- proxy_rules.version
- output_template.version
- evidence_policy.version
- inference_policy.version

## 反馈闭环

- 人工修改 G 列主指标 -> 触发证据表/推测留痕同步检查。
- 人工确认室分状态 -> 更新 BuildStatus 并降低未来类似场景不确定性。
- 人工关闭 Review Item -> 记录复核动作和证据。
- 发现错误来源 -> source reliability 降权。

## 自动重扫策略

- Tier 1 关键来源：每 30-90 天复查。
- 规划/在建项目：每 14-30 天复查。
- Review Queue 未关闭：每 7-14 天复查。
- 已验证稳定资产：每 90-180 天复查。
