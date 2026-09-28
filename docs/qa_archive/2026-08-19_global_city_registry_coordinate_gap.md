# 2026-08-19 Global City Registry Coordinate Gap

## Incident

全球城市规范化首版只加载带参考坐标的 UN/LOCODE 记录。Kabul、Tirana 等
官方地点名称虽存在，但因记录没有坐标而被提前丢弃，最终误报为
`official_name_not_found`。同一问题也使 `El Qahira (Cairo)`、
`Jakarta, Java` 等官方标签中的明确别名无法命中。

## Root Cause

- `load_unlocode_locations()` 在名称匹配前过滤无坐标记录。
- 审计只输出粗粒度 `official_name_not_found`，没有区分行政区误填、名称缺失和
  坐标冲突。
- 国家级严格发布门同时被错误复用于本地 assignment 写入，导致一个国家只要有
  少量 unresolved，其他 verified 物业也没有 `city_id`。
- UN/LOCODE 中 Pleiku 等同名、近坐标的重复代码会在下游形成假 ambiguity。

## Controls Implemented

- 保留无坐标地点；仅当官方名称或受控官方别名在该国唯一时自动 verified。
- 括号内容、方括号内容和逗号前主名才可生成官方别名；不做自由模糊匹配。
- 有官方参考坐标时继续执行 75 km 一致性门；冲突不得降级为名称匹配。
- 使用 UN/LOCODE subdivision 注册表识别省、州、地区等高层行政区，并写入
  `high_level_admin_area_not_city`。
- 同名且参考点 5 km 内的重复官方代码确定性合并，并保留 duplicate codes。
- `--apply-verified --all-countries` 只写入 verified 物业，同时持久化 unresolved
  原因；有 unresolved 的国家仍禁止发布。
- 批量计划遇到并发新增物业时安全跳过并报告 `requires_rerun`，不得因 stale
  snapshot 崩溃或错误宣告 publication ready。

## Verification

- 自动控制：`QA-ENTITY-002`。
- 规则代码：`src/isite2/growth/global_city_registry.py`、
  `src/isite2/growth/city_normalization.py`、
  `scripts/run_city_normalization_refresh.py`。
- 回归测试：`tests/test_city_normalization.py` 覆盖无坐标唯一名称、受控别名、
  行政区拒绝、重复官方代码、精确 unresolved 原因和 verified-only overlay 写入。
- 最终本地审计：7,152 个物业中 5,593 verified、1,559 unresolved、0 identity
  collision、0 concurrent skip。未解决项继续按国家阻断发布。

## 2026-08-24 Follow-up: Official Name Variants And Identity Collisions

重建当前 7,245 条物业时发现，UN/LOCODE 的 `Moscow = Moskva` 变更记录未被
加载，且 `Munich / München` 等官方名称变体因“必须先名称精确命中”被误判为
缺失。逐物业近邻尝试又会让同一个 `Athens` 分别匹配城区、港区和机场附近的
不同 UN/LOCODE 点。

修复后的确定性顺序为：先解析 UN/LOCODE 官方 `=` 别名；再对非 subdivision
名称执行词形相似和 75 km 坐标双门；同一 `source_city` 先整组选定唯一官方主点，
再逐物业校验距离。低相似名称、同分近邻、坐标冲突和高阶行政区仍保持阻断。
该规则把 unresolved 从 1,563 降到 802，并暴露 22 组同名、同场景、同坐标的
历史重复物业。重复组经数据库备份后合并，所有子表外键迁移，最终 identity
collision 为 0。

`publish_public_country_delta.py` 新增城市硬门：目标范围内每个物业必须有
`city_id`、verified 且一致的 assignment，且不得存在 identity collision。
回归覆盖见 `tests/test_city_normalization.py` 和
`tests/test_public_country_delta.py`；合并审计见
`outputs/city_normalization/exact_identity_merge_20260824T074851Z.json`。

## 2026-08-24 Follow-up: UN/LOCODE Coverage Blind Spots

UN/LOCODE 是贸易与运输地点标准，不是完整的居民地名录。仅靠它会遗漏城区、
地方城市和部分官方别名；直接使用最近坐标又会把机场归到邻近小镇，违背主城市
口径。修复采用双层权威来源：UN/LOCODE 继续优先，只有其未验证时才查询 NGA
GNS 的官方 populated place 记录。GNS 仅接纳 `FC=P`，按稳定 UFI 聚合批准名与
变体名，并要求名称/官方别名精确命中和 75 km 坐标一致；高阶行政区、纯近邻、
坐标冲突和 2013 年后新城继续阻断。每个 city 单独保存来源，禁止把 GNS 结果
错误标成 UNECE。

本轮官方包逐国校验 ZIP 和 SHA-256 后，verified 从 6,481 增至 6,753，unresolved
从 742 降至 469，identity collision 保持 0，发布就绪国家从 41 增至 63。规则与
回归测试继续归入 `QA-ENTITY-002`；下载清单在
`.tmp/gns-2013-12/download_manifest.json`，registry 审计在
`outputs/city_normalization/global_registry_audit_with_gns_20260824.json`，全库
落库审计在
`outputs/city_normalization/global_gns_apply_verified_20260824.json`。

## 2026-08-24 Follow-up: Property-name City Hints And Stale Verification

高阶行政区或错误旧城市会掩盖物业名称中的真实服务城市，但“名称包含地名”本身
会把大学名、人名机场和商场品牌误当城市。修复只接受：官方地名位于物业名开头
（或受控的 `Bahías de` 地理前缀后）、后接当前场景设施词、官方实体名称不含设施
词、坐标唯一且在场景距离门内。普通 GNS populated place 使用 25 km 门，只有官方
行政中心和 UN/LOCODE 可使用 75 km。映射以 property ID、名称和场景绑定的
`property_overrides` 保存；省/州名不得写入城市 alias。

UN/LOCODE 的“名称唯一但无坐标”结果若能在 GNS 找到同名官方点，必须先交叉校验；
坐标冲突时旧 verified 降级并清除 `city_id`，不得继续用历史缓存掩盖。跨来源同城
优先复用 UN/LOCODE city_id。新暴露的精确 identity collision 只能在名称、场景、
verified city_id 一致且坐标不超过 75 km 时合并；合并前备份数据库，迁移所有
property 子表，唯一键冲突去重，且不得新增外键错误。

规则实现和回归继续归入 `QA-ENTITY-002`。最终审计：7,178 个物业，6,844
verified、334 unresolved、0 identity collision；44 条历史重复已审计合并。主要
artifact 为
`outputs/city_normalization/global_gns_crosscheck_apply_20260824.json` 和
`outputs/city_normalization/global_property_override_apply_20260824.identity_merge.json`。

## 2026-08-24 Follow-up: Unique Nearest Official Settlement

剩余缺口以省/州/大区误作城市、机场名称不含服务城市，以及官方名录覆盖不足为主。
仅用“最近 GNS populated place”会产生新的跨省错误，例如把位于 Kandal Province
的新机场归到邻省 Takêv；过宽机场半径也会在官方包缺少 Espargos 时误选 Santa
Maria。因此坐标近邻不能作为无条件兜底。

新增规则只处理仍为 `unmapped` 且有坐标的物业，不覆盖已有坐标冲突。每国 GNS
地点索引只展开一次并复用；优先选择官方行政中心，普通 populated place 使用更短
半径，最近与次近候选必须同时满足至少 5 km 距离差和 1.5 倍距离比。若原始地点能
匹配 GNS 官方 ADM1 名称，候选必须属于同一 ADM1；有该约束的机场上限为 25 km，
无 ADM1 约束时上限收紧为 15 km。无坐标、跨 ADM1、过远或密集双城均继续进入
Review Queue。结果始终写成物业级 override，省/州名不得进入规范城市 alias。

该规则继续归入 `QA-ENTITY-002`，实现位于
`src/isite2/growth/global_city_registry.py`。回归测试覆盖唯一行政中心、跨 ADM1、
密集双城、距离门、无坐标、物业级 override 和无约束机场半径，见
`tests/test_city_normalization.py`。
