from __future__ import annotations

import httpx
import json
import os
import subprocess
import sys

from isite2.connectors.firecrawl import (
    FakeFirecrawlPublicEvidenceProvider,
    FirecrawlPublicEvidenceProvider,
    firecrawl_subprocess_env,
)


def test_firecrawl_provider_disabled_without_api_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_CLI_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("FIRECRAWL_ENABLED", "true")

    provider = FirecrawlPublicEvidenceProvider(deployment="cloud")

    assert provider.enabled is False
    assert provider.search("airport passenger traffic") == []


def test_firecrawl_provider_defaults_to_local_without_api_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("ISITE2_FIRECRAWL_DEPLOYMENT", raising=False)
    monkeypatch.delenv("FIRECRAWL_ENABLED", raising=False)
    monkeypatch.setenv("FIRECRAWL_CLI_CREDENTIALS_PATH", str(tmp_path / "missing.json"))

    provider = FirecrawlPublicEvidenceProvider()

    assert provider.enabled is True
    assert provider.deployment == "local"
    assert provider.base_url == "http://127.0.0.1:3002/v1"
    assert "Authorization" not in provider._headers()


def test_firecrawl_provider_reads_cli_credentials_when_env_key_missing(
    monkeypatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text(
        '{"apiKey":"fc-cli-test-key","apiUrl":"https://api.firecrawl.dev"}',
        encoding="utf-8",
    )
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_CLI_CREDENTIALS_PATH", str(credentials_path))
    monkeypatch.setenv("FIRECRAWL_ENABLED", "true")

    provider = FirecrawlPublicEvidenceProvider()

    assert provider.enabled is True
    assert provider.api_key == "fc-cli-test-key"


def test_firecrawl_provider_ignores_stale_env_proxy_by_default(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("FIRECRAWL_TRUST_ENV_PROXY", raising=False)

    provider = FirecrawlPublicEvidenceProvider(api_key="fc-test", enabled=True)

    assert provider.client.trust_env is False


def test_firecrawl_cli_env_strips_proxy_vars_unless_opted_in(monkeypatch) -> None:
    env = {
        "PATH": "/usr/bin",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "https_proxy": "http://127.0.0.1:7890",
        "FIRECRAWL_API_KEY": "fc-test",
    }
    monkeypatch.delenv("FIRECRAWL_TRUST_ENV_PROXY", raising=False)

    cleaned = firecrawl_subprocess_env(env)

    assert "HTTP_PROXY" not in cleaned
    assert "https_proxy" not in cleaned
    assert cleaned["FIRECRAWL_API_KEY"] == "fc-test"

    monkeypatch.setenv("FIRECRAWL_TRUST_ENV_PROXY", "1")
    assert firecrawl_subprocess_env(env)["HTTP_PROXY"] == "http://127.0.0.1:7890"


def test_firecrawl_cli_wrapper_print_env_strips_proxy_vars(tmp_path) -> None:
    env = dict(os.environ)
    env["HTTP_PROXY"] = "http://127.0.0.1:7890"
    env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    env["FIRECRAWL_CLI_CREDENTIALS_PATH"] = str(tmp_path / "missing.json")
    env.pop("FIRECRAWL_TRUST_ENV_PROXY", None)

    completed = subprocess.run(
        [sys.executable, "scripts/run_firecrawl_cli.py", "--print-env"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(completed.stdout)

    assert payload["deployment"] == "local"
    assert payload["cloud_credits_expected"] == 0
    assert payload["proxy_vars_present_after_sanitize"]["HTTP_PROXY"] is False
    assert payload["proxy_vars_present_after_sanitize"]["HTTPS_PROXY"] is False


def test_fake_firecrawl_search_scrape_normalizes_to_connector_models() -> None:
    provider = FakeFirecrawlPublicEvidenceProvider(
        search_pages=[
            {
                "title": "Algiers airport official report",
                "url": "https://airport.example.dz/report",
                "description": "Passenger statistics.",
                "markdown": "The airport handled 8,000,000 passengers in 2024.",
                "metadata": {
                    "sourceURL": "https://airport.example.dz/report",
                    "publishedTime": "2025-01-01",
                },
            }
        ]
    )

    results = provider.search("Algeria airport passenger statistics", limit=1)
    page = provider.fetch_page(str(results[0].url))

    assert results[0].source_name == "Firecrawl Search"
    assert page.source_url == "https://airport.example.dz/report"
    assert page.source_date == "2025-01-01"
    assert "8,000,000 passengers" in page.content_text
    assert page.robots_allowed is True


def test_firecrawl_search_payload_uses_egypt_markdown_scrape_options() -> None:
    client = RecordingFirecrawlClient(
        [
            _json_response(
                {
                    "data": {
                        "web": [
                            {
                                "title": "Cairo airport official passenger report",
                                "url": "https://airport.example.eg/report",
                                "description": "Passenger statistics.",
                                "markdown": "The airport handled 12,000,000 passengers in 2024.",
                                "metadata": {
                                    "sourceURL": "https://airport.example.eg/report",
                                    "publishedTime": "2025-01-01",
                                },
                            }
                        ]
                    },
                    "creditsUsed": 2,
                    "warning": "sample warning",
                }
            )
        ]
    )
    provider = FirecrawlPublicEvidenceProvider(
        api_key="fc-test",
        enabled=True,
        deployment="cloud",
        client=client,
        sleep_func=lambda _: None,
    )

    results = provider.search("Egypt airport passenger traffic", limit=1)
    page = provider.fetch_page(str(results[0].url))
    payload = client.requests[0]["json"]

    assert payload["country"] == "EG"
    assert payload["location"] == "Egypt"
    assert payload["sources"] == ["web"]
    assert payload["scrapeOptions"]["formats"] == [{"type": "markdown"}]
    assert payload["scrapeOptions"]["onlyMainContent"] is True
    assert payload["scrapeOptions"]["removeBase64Images"] is True
    assert payload["scrapeOptions"]["blockAds"] is True
    assert len(client.requests) == 1
    assert page.source_url == "https://airport.example.eg/report"
    assert "12,000,000 passengers" in page.content_text
    assert provider.stats.search_requests == 1
    assert provider.stats.credits_used == 2
    assert provider.stats.warnings == ["sample warning"]


def test_firecrawl_scrape_parses_data_shape_and_retries_retryable_status() -> None:
    sleep_calls: list[float] = []
    client = RecordingFirecrawlClient(
        [
            _json_response({}, status_code=429, headers={"Retry-After": "0"}),
            _json_response(
                {
                    "data": {
                        "markdown": "Egypt venue has 30,000 sqm exhibition space.",
                        "metadata": {
                            "sourceURL": "https://venue.example.eg/profile",
                            "publishedTime": "2025-02-01",
                        },
                    },
                    "creditsUsed": 1,
                }
            ),
        ]
    )
    provider = FirecrawlPublicEvidenceProvider(
        api_key="fc-test",
        enabled=True,
        deployment="cloud",
        client=client,
        max_attempts=2,
        sleep_func=sleep_calls.append,
    )

    page = provider.fetch_page("https://venue.example.eg/profile")

    assert len(client.requests) == 2
    assert client.requests[0]["json"]["formats"] == [{"type": "markdown"}]
    assert sleep_calls == [0.0]
    assert page.source_url == "https://venue.example.eg/profile"
    assert "30,000 sqm" in page.content_text
    assert provider.stats.scrape_requests == 1


def test_local_firecrawl_scrape_uses_v1_string_format_and_zero_cloud_credits() -> None:
    client = RecordingFirecrawlClient(
        [
            _json_response(
                {
                    "success": True,
                    "data": {
                        "markdown": "The venue has 20,000 square metres.",
                        "metadata": {"sourceURL": "https://venue.example.gh/"},
                    },
                    "creditsUsed": 1,
                }
            )
        ]
    )
    provider = FirecrawlPublicEvidenceProvider(
        enabled=True,
        deployment="local",
        client=client,
    )

    provider.fetch_page("https://venue.example.gh/")

    assert client.requests[0]["json"]["formats"] == ["markdown"]
    assert "Authorization" not in client.requests[0]["headers"]
    assert provider.stats.credits_used == 0


def test_local_firecrawl_search_omits_cloud_only_fields_and_deferred_scrape() -> None:
    client = RecordingFirecrawlClient(
        [
            _json_response(
                {
                    "success": True,
                    "data": [
                        {
                            "title": "Local search result",
                            "url": "https://venue.example.kz/",
                            "description": "Official venue profile.",
                        }
                    ],
                }
            )
        ]
    )
    provider = FirecrawlPublicEvidenceProvider(
        enabled=True,
        deployment="local",
        client=client,
    )

    provider.search("Kazakhstan venue capacity", limit=1)

    payload = client.requests[0]["json"]
    assert "sources" not in payload
    assert "scrapeOptions" not in payload


class RecordingFirecrawlClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def post(self, url: str, json: dict, headers: dict) -> httpx.Response:
        self.requests.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def _json_response(
    payload: dict,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=payload,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.firecrawl.dev/v2/test"),
    )
