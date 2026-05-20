# Codex Task 06 — Search/Crawler Connectors and Source Cache

## Goal

Add compliant public evidence connector infrastructure.

## Implement

- Provider interface: search, fetch_page, geocode.
- Source cache table/service.
- Rate limiter.
- robots.txt check interface.
- Source tier classifier.
- Evidence extraction result model.

## Rules

- Do not bypass login, captcha, paywall, robots.txt, or anti-scraping controls.
- Do not store unrelated personal data.
- Store source_url, source_name, source_tier, source_date, fetched_at.
- Failed evidence extraction writes Review Queue.
