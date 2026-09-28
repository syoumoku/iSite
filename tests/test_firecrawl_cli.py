import json

from scripts import run_firecrawl_cli
from scripts.run_firecrawl_cli import _normalize_searxng_image_results


def test_normalize_searxng_image_results_for_firecrawl_image_consumers() -> None:
    payload = {
        "results": [
            {
                "url": "https://venue.example/about",
                "title": "Example Venue exterior",
                "img_src": "https://venue.example/images/exterior.jpg",
                "resolution": "1344 x 768",
            },
            {
                "url": "https://venue.example/empty",
                "title": "Missing image",
                "img_src": "",
            },
        ]
    }

    result = _normalize_searxng_image_results(payload, limit=5)

    assert result == [
        {
            "imageUrl": "https://venue.example/images/exterior.jpg",
            "url": "https://venue.example/about",
            "title": "Example Venue exterior",
            "imageWidth": 1344,
            "imageHeight": 768,
        }
    ]


def test_batch_image_search_retains_one_artifact_per_query(monkeypatch, tmp_path) -> None:
    rows = [
        {"query": "first property", "output_path": str(tmp_path / "first.json")},
        {"query": "second property", "output_path": str(tmp_path / "second.json")},
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(
        run_firecrawl_cli,
        "_local_image_search_payload",
        lambda query, limit, timeout: {
            "success": True,
            "deployment": "local",
            "cloudCreditsUsed": 0,
            "data": {"images": [{"title": query}]},
        },
    )

    summary = run_firecrawl_cli._run_local_image_search_batch(
        manifest,
        limit=5,
        timeout=2,
        max_workers=2,
    )

    assert summary["completed_count"] == 2
    assert summary["failed_count"] == 0
    assert json.loads((tmp_path / "first.json").read_text())["cloudCreditsUsed"] == 0
