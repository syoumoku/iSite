#!/bin/sh
set -eu

# The upstream image falls back to DuckDuckGo when SearXNG returns no rows.
# iSite discovery is intentionally restricted to the configured local metasearch.
for file in /app/dist/src/search/index.js /app/dist/src/search/v2/index.js; do
  if [ -f "$file" ]; then
    sed -i \
      's/logger\.info("Using DuckDuckGo search");/logger.info("SearXNG returned no results; external fallback disabled"); return {};/g' \
      "$file"
  fi
done

exec node dist/src/harness.js --start-docker
