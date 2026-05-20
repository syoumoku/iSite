# 世界地图 UI 规格

MVP 静态 UI 位于 `ui/world_map/`，由 FastAPI 挂载到 `/ui/`。

## 页面结构

1. 顶部栏：产品名、导出批次、国家/区域选择、导出入口。
2. 左侧筛选：
   - Country / Region / City
   - Scene Type
   - Evidence Status
   - Value Class
   - Action Class
   - Indoor System Presence
   - Indoor RAT
   - Proxy Level
   - Has Review Issue
3. 主地图：国家聚合、城市聚合、数据库全量候选物业点 marker。
4. 右侧详情 Drawer：点击点位打开。
5. 底部 KPI：候选数量、Verified/Supported 占比、Direct Recommend 数量、Unknown Build Status 数量。

## Marker 数据

默认不传 `scan_run_id`，地图展示数据库中所有 canonical property 的最新 packet 视图；`scan_run_id` 只用于历史批次调试或单批次导出链路。

GeoJSON Feature properties 至少包含：

- property_id
- property_name
- country
- city
- scene_type
- evidence_status
- value_class
- action_class
- recommended_solution
- indoor_system_presence
- indoor_rat
- main_metric_text
- review_count
- last_scan_at

## 详情 Drawer

- 基本信息：名称、国家、城市、经纬度、Google Maps link。
- 核心指标：G 列主指标、年访问量 proxy、忙时流量。
- 结论：证据状态、价值等级、行动类别、推荐方案。
- 现网状态：有无室分、建设制式、运营商、室分类型。
- 证据：来源名称、等级、日期、链接、交叉校验状态。
- 推测：推测字段、推测值、推测链路、置信度。
- 复核：具体原因和下一步动作。

## 交互

- Map marker clustering by zoom.
- Filter changes update KPI and marker set.
- Click country shows country summary.
- Click property opens drawer.
- Export selected scan run to Excel.
- Generate PPT insight cards for selected scan run.
