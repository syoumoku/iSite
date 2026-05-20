# 公开证据采集与合规策略

## 允许

- 官方网站、政府网站、机场/场馆/医院/学校/开发商公开页面。
- 年报、ESG、投资者材料、监管公告。
- 可公开访问的地图、OTA、活动平台、地产平台页面。
- 合规搜索 API 或自有企业授权数据源。

## 不允许

- 绕过登录、验证码、付费墙、反爬措施。
- 采集与楼宇机会识别无关的个人数据。
- 对目标网络做端口扫描、漏洞扫描、入侵式探测。
- 把不稳定网页内容当作不可变事实。

## 强制记录

每条证据至少记录：

- source_url
- source_name
- source_tier
- source_date
- fetched_at
- field_group
- field_value
- direct/proxy/inferred
- cross_check_status

## 采集策略

- 官方 API 优先。
- 同域名限速。
- source cache 防重复访问。
- 抓取失败不硬编，进入 Review Queue。
- 证据过期触发 freshness job。
