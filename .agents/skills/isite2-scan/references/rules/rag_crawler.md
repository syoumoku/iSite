# iSite2 RAG And Crawler Rules

Firecrawl is a provider adapter, not a bypass around iSite2 evidence controls.

Crawler rules:
- Respect public-data compliance: no login bypass, captcha bypass, paywall bypass, robots violation, invasive probing, or unrelated personal data.
- Firecrawl Search/Map/Crawl/Scrape/Extract output must normalize into existing connector models and then flow through source cache, raw evidence, curation, and Review Queue.
- Extract can propose candidate fields but cannot directly write final value/action/build conclusions.
- Store source URL, source name, source tier, source date, fetched date, content hash, field group, field value, and discovery task metadata.

RAG rules:
- RAG answers must cite indexed evidence chunks.
- Citation must include source URL/name/tier/date/fetched date and either raw evidence ID or property ID.
- No citation means no strong conclusion.
- RAG may recommend priority bands, not unified scores.
- Priority recommendations must explain evidence used, evidence gaps, and concrete next actions.
- Query should apply structured filters first: scan run, property, country, scene.
- Retrieval should prefer source tier, recency, field relevance, and property packet context.
