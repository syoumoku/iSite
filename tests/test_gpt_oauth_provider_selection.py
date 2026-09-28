from __future__ import annotations

import sys
from pathlib import Path

import pytest

from isite2.growth import derived_refresh, raw_evidence_metric_extraction
from isite2.rules import metric_identity


def test_auto_provider_selection_prefers_codex_oauth_over_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_codex_oauth(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert isinstance(
        derived_refresh._provider_from_mode("auto", cache_dir=None),
        derived_refresh.CodexOAuthDerivedInfoProvider,
    )
    assert isinstance(
        raw_evidence_metric_extraction.raw_metric_provider_from_mode("auto"),
        raw_evidence_metric_extraction.CodexOAuthRawMetricExtractionProvider,
    )
    assert isinstance(
        metric_identity.recommendation_metric_identity_provider_from_mode("auto"),
        metric_identity.CodexOAuthRecommendationMetricIdentityProvider,
    )


def test_auto_provider_selection_does_not_use_api_key_without_oauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    derived_provider = derived_refresh._provider_from_mode("auto", cache_dir=None)

    assert isinstance(derived_provider, derived_refresh.RuleSafetyFallbackProvider)
    with pytest.raises(ValueError):
        metric_identity.recommendation_metric_identity_provider_from_mode("auto")


def test_recommendation_metric_env_defaults_to_cached_codex_oauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_codex_oauth(monkeypatch, tmp_path)
    monkeypatch.setenv("ISITE2_RECOMMENDATION_METRIC_GPT", "auto")
    monkeypatch.delenv("ISITE2_RECOMMENDATION_METRIC_GPT_PROVIDER", raising=False)
    monkeypatch.setenv(
        "ISITE2_RECOMMENDATION_METRIC_CACHE_DIR",
        str(tmp_path / "recommendation_metric_cache"),
    )

    provider = metric_identity.recommendation_metric_identity_provider_from_env()

    assert isinstance(provider, metric_identity.FileCachedMetricIdentityProvider)
    assert isinstance(
        provider.provider,
        metric_identity.CodexOAuthRecommendationMetricIdentityProvider,
    )


def test_openai_api_key_path_requires_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert isinstance(
        derived_refresh._provider_from_mode("gpt", cache_dir=None),
        derived_refresh.OpenAIDerivedInfoProvider,
    )
    assert isinstance(
        raw_evidence_metric_extraction.raw_metric_provider_from_mode("gpt"),
        raw_evidence_metric_extraction.OpenAIRawMetricExtractionProvider,
    )
    assert isinstance(
        metric_identity.recommendation_metric_identity_provider_from_mode("gpt"),
        metric_identity.OpenAIRecommendationMetricIdentityProvider,
    )


def _prepare_codex_oauth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(derived_refresh.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(raw_evidence_metric_extraction.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(metric_identity.shutil, "which", lambda _: sys.executable)
