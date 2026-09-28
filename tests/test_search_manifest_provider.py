from __future__ import annotations

import json

import pytest

from isite2.connectors.web import SearchManifestProvider, SearchManifestValidationError


def test_search_manifest_provider_replays_adopted_rows(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "language": "ru",
                        "search_channel": "Yandex",
                        "query": "airport passenger traffic",
                        "title": "Airport traffic report",
                        "url": "https://airport.example/report",
                        "snippet": "12 million passengers",
                        "adopted": True,
                        "skipped_reason": "",
                        "target_scene": "airport_terminal",
                    },
                    {
                        "language": "ru",
                        "search_channel": "Yandex",
                        "query": "airport passenger traffic",
                        "title": "Unrelated result",
                        "url": "https://example.com/unrelated",
                        "snippet": "",
                        "adopted": False,
                        "skipped_reason": "off-target",
                        "target_scene": "airport_terminal",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    provider = SearchManifestProvider([manifest_path])
    results = provider.search("airport passenger traffic", limit=5)

    assert provider.enabled
    assert len(results) == 1
    assert str(results[0].url) == "https://airport.example/report"
    assert results[0].source_name == "Search Manifest"


def test_search_manifest_requires_auditable_localization_fields(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "query": "mall gla",
                    "title": "Mall facts",
                    "url": "https://mall.example/facts",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SearchManifestValidationError):
        SearchManifestProvider([manifest_path])
