from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol

import httpx

DEFAULT_RECOMMENDATION_METRIC_CACHE_DIR = Path("outputs/cache/recommendation_metric_identity")
DEFAULT_MIN_CONFIDENCE = 0.72


class MetricIdentityProvider(Protocol):
    provider_name: str

    def identify(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON decision for one metric-label normalization request."""


class OpenAIRecommendationMetricIdentityProvider:
    provider_name = "openai_gpt_recommendation_metric_identity"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ISITE2_OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY or ISITE2_OPENAI_API_KEY is required for GPT metric identity"
            )
        self.model = (
            model
            or os.getenv("ISITE2_RECOMMENDATION_METRIC_OPENAI_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        self.timeout_seconds = timeout_seconds

    def identify(self, request: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _METRIC_IDENTITY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(request, ensure_ascii=False, sort_keys=True),
                    },
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        decision = json.loads(payload["choices"][0]["message"]["content"])
        decision.setdefault("provider_type", "gpt")
        decision.setdefault("provider_model", self.model)
        return decision


class CodexOAuthRecommendationMetricIdentityProvider:
    provider_name = "codex_oauth_recommendation_metric_identity"

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.codex_bin = (
            codex_bin
            or os.getenv("ISITE2_CODEX_BIN")
            or shutil.which("codex")
            or "/Applications/Codex.app/Contents/Resources/codex"
        )
        if not Path(self.codex_bin).exists() and shutil.which(self.codex_bin) is None:
            raise RuntimeError(f"codex executable not found: {self.codex_bin}")
        if not _codex_oauth_available():
            raise RuntimeError("Codex OAuth auth not found. Run `codex login` first.")
        self.model = (
            model
            or os.getenv("ISITE2_RECOMMENDATION_METRIC_CODEX_MODEL")
            or os.getenv("ISITE2_CODEX_OAUTH_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or "gpt-5.4-mini"
        )
        self.reasoning_effort = (
            reasoning_effort
            or os.getenv("ISITE2_RECOMMENDATION_METRIC_REASONING_EFFORT")
            or os.getenv("ISITE2_CODEX_OAUTH_REASONING_EFFORT")
            or "low"
        )
        self.timeout_seconds = timeout_seconds

    def identify(self, request: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="isite2_metric_identity_") as directory:
            temp_dir = Path(directory)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / "metric_identity_decision.json"
            schema_path.write_text(
                json.dumps(_METRIC_IDENTITY_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prompt = "\n\n".join(
                [
                    _METRIC_IDENTITY_SYSTEM_PROMPT,
                    "Return only a JSON object matching the provided schema.",
                    json.dumps(request, ensure_ascii=False, sort_keys=True),
                ]
            )
            command = [
                self.codex_bin,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-rules",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "-m",
                self.model,
                "-c",
                f"model_reasoning_effort='{self.reasoning_effort}'",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-C",
                str(temp_dir),
                "-",
            ]
            process = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if process.returncode != 0:
                stderr = "\n".join(
                    line for line in process.stderr.splitlines()[-8:] if line.strip()
                )
                raise RuntimeError(
                    "Codex OAuth recommendation metric identity failed with exit "
                    f"{process.returncode}: {stderr}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex OAuth recommendation metric identity produced no JSON")
            decision = json.loads(output_path.read_text(encoding="utf-8"))
            decision.setdefault("provider_type", "gpt")
            decision.setdefault("provider_model", f"codex-oauth:{self.model}")
            return decision


class FileCachedMetricIdentityProvider:
    def __init__(
        self,
        provider: MetricIdentityProvider,
        cache_dir: Path = DEFAULT_RECOMMENDATION_METRIC_CACHE_DIR,
    ) -> None:
        self.provider = provider
        self.cache_dir = cache_dir
        self.provider_name = f"cached:{provider.provider_name}"

    def identify(self, request: dict[str, Any]) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{_cache_key(request)}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached["cache_status"] = "hit"
            return cached
        decision = self.provider.identify(request)
        decision["cache_status"] = "miss"
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
        return decision


def recommendation_metric_identity_provider_from_env() -> MetricIdentityProvider | None:
    enabled = os.getenv("ISITE2_RECOMMENDATION_METRIC_GPT")
    if enabled is None and os.getenv("PYTEST_CURRENT_TEST"):
        enabled = "0"
    enabled = str(enabled or "auto").strip().casefold()
    if enabled in {"0", "false", "no", "off"}:
        return None
    if enabled in {"", "auto"} and not _codex_oauth_available():
        return None
    provider_mode = os.getenv("ISITE2_RECOMMENDATION_METRIC_GPT_PROVIDER", "codex-oauth")
    provider = recommendation_metric_identity_provider_from_mode(provider_mode)
    if os.getenv("ISITE2_RECOMMENDATION_METRIC_GPT_NO_CACHE"):
        return provider
    cache_dir = Path(
        os.getenv(
            "ISITE2_RECOMMENDATION_METRIC_CACHE_DIR",
            str(DEFAULT_RECOMMENDATION_METRIC_CACHE_DIR),
        )
    )
    return FileCachedMetricIdentityProvider(provider, cache_dir)


def recommendation_metric_identity_provider_from_mode(mode: str) -> MetricIdentityProvider:
    selected = mode.strip().casefold()
    if selected == "codex-oauth":
        return CodexOAuthRecommendationMetricIdentityProvider()
    if selected in {"gpt", "openai"}:
        return OpenAIRecommendationMetricIdentityProvider()
    if selected == "auto":
        if _codex_oauth_available():
            return CodexOAuthRecommendationMetricIdentityProvider()
    raise ValueError(
        "recommendation metric provider mode must be one of: auto, gpt, openai, codex-oauth"
    )


def accepted_metric_identity(
    decision: dict[str, Any],
    *,
    allowed_metric_keys: set[str],
    min_confidence: float | None = None,
) -> str | None:
    if not bool(decision.get("recognized")):
        return None
    if not bool(decision.get("is_scene_primary_metric")):
        return None
    confidence = _float_or_none(decision.get("confidence"))
    if confidence is None or confidence < (min_confidence or DEFAULT_MIN_CONFIDENCE):
        return None
    metric_key = _normalize_key(str(decision.get("canonical_metric_key") or ""))
    if metric_key not in allowed_metric_keys:
        return None
    return metric_key


def metric_identity_request(
    *,
    scene_type: str,
    field_group: str,
    indicator_name: str,
    field_value: str,
    allowed_metric_keys: set[str],
    preferred_metric_keys: set[str],
) -> dict[str, Any]:
    return {
        "task": "normalize_scene_metric_label_for_iSite2_recommendation_gate",
        "scene_type": scene_type,
        "raw_metric": {
            "field_group": field_group,
            "indicator_name": indicator_name,
            "field_value": field_value,
        },
        "allowed_canonical_metric_keys": sorted(allowed_metric_keys),
        "preferred_gate_metric_keys": sorted(preferred_metric_keys),
        "return_schema": {
            "recognized": "boolean",
            "canonical_metric_key": "one of allowed_canonical_metric_keys or null",
            "is_scene_primary_metric": "boolean",
            "confidence": "number from 0 to 1",
            "reason": "short audit reason",
        },
    }


def _cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_key(value: str) -> str:
    import re

    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_"))


def _codex_oauth_available() -> bool:
    return (Path.home() / ".codex" / "auth.json").exists()


_METRIC_IDENTITY_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "recognized": {"type": "boolean"},
        "canonical_metric_key": {"type": ["string", "null"]},
        "is_scene_primary_metric": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": [
        "recognized",
        "canonical_metric_key",
        "is_scene_primary_metric",
        "confidence",
        "reason",
    ],
}


_METRIC_IDENTITY_SYSTEM_PROMPT = """You are the iSite2 recommendation metric normalizer.

Map one evidence metric label to a canonical scene metric key for a recommendation
gate. Return only a JSON object. You must choose canonical_metric_key only from
allowed_canonical_metric_keys, otherwise return null and recognized=false.

Rules:
- Use the field_group, indicator_name and field_value text. Do not infer from
  property prestige or country.
- A valid match must be a quantitative, scene-level primary metric.
- For mall_mixed_use, labels such as retail_gfa, retail gross floor area,
  retail floor area, gross leasable area, gross lettable area, GLA, ABL, or
  área bruta locável can map to gla when the value is a retail/leasable area.
- Do not map descriptive labels such as flagship position, anchor brands,
  urban catchment, gateway role or CBD role to a quantitative metric.
- If uncertain, return recognized=false with a short reason.
"""
