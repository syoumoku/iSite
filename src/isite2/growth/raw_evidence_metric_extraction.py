from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

import httpx

from isite2.growth.derived_refresh import (
    QUANTITATIVE_PRIMARY,
    _normalize_key,
    _numeric_value_and_unit,
)
from isite2.growth.evidence_intake import CandidateDraft
from isite2.rules.config_loader import get_scene_rule

RAW_METRIC_EXTRACTION_SCHEMA_VERSION = "raw_metric_extraction_v1"
DEFAULT_RAW_METRIC_CACHE_DIR = Path("outputs") / "raw_metric_extraction" / "gpt_cache"
MIN_GPT_METRIC_CONFIDENCE = 0.72
MAX_CONTEXT_CHARS = 8_000


class RawMetricExtractionProvider(Protocol):
    provider_name: str

    def extract(self, packet: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class RawMetricCleaningResult:
    draft: CandidateDraft
    status: str
    provider_name: str | None = None
    cache_status: str | None = None
    issues: list[str] = field(default_factory=list)
    decision: dict[str, Any] | None = None


class RawEvidenceMetricCleaner:
    """Rule-first metric cleaner for raw evidence curation.

    GPT is only consulted for ambiguous raw text. Accepted GPT output still has to
    pass the local scene-primary whitelist and numeric parser before the draft is
    changed.
    """

    def __init__(
        self,
        provider: RawMetricExtractionProvider | None = None,
        *,
        cache_dir: Path | None = DEFAULT_RAW_METRIC_CACHE_DIR,
        min_confidence: float = MIN_GPT_METRIC_CONFIDENCE,
    ) -> None:
        if provider is not None and cache_dir is not None:
            provider = FileCachedRawMetricExtractionProvider(provider, cache_dir)
        self.provider = provider
        self.min_confidence = min_confidence

    @property
    def provider_name(self) -> str | None:
        return self.provider.provider_name if self.provider else None

    def clean(self, raw: Any, draft: CandidateDraft) -> RawMetricCleaningResult:
        content_text = _raw_content_text(raw, draft)
        if not _needs_gpt_metric_cleaning(draft, content_text):
            return RawMetricCleaningResult(draft=draft, status="rules_accepted")
        if self.provider is None:
            return RawMetricCleaningResult(
                draft=draft,
                status="gpt_unavailable",
                issues=["raw metric cleaning needs GPT but no provider is configured"],
            )

        packet = _metric_packet(raw, draft, content_text)
        decision = self.provider.extract(packet)
        validation = _validate_gpt_metric_decision(
            decision,
            scene_type=draft.scene_type,
            min_confidence=self.min_confidence,
        )
        if validation["accepted"]:
            cleaned = replace(
                draft,
                field_group=validation["field_group"],
                indicator_name=validation["indicator_name"],
                field_value=validation["field_value"],
                assumption_note=_assumption_note(decision),
            )
            return RawMetricCleaningResult(
                draft=cleaned,
                status="gpt_applied",
                provider_name=self.provider.provider_name,
                cache_status=decision.get("cache_status"),
                decision=decision,
            )
        return RawMetricCleaningResult(
            draft=draft,
            status="gpt_rejected",
            provider_name=self.provider.provider_name,
            cache_status=decision.get("cache_status"),
            issues=[validation["reason"]],
            decision=decision,
        )


class OpenAIRawMetricExtractionProvider:
    provider_name = "openai_gpt_raw_metric"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ISITE2_OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY or ISITE2_OPENAI_API_KEY is required for GPT raw metric extraction"
            )
        self.model = (
            model
            or os.getenv("ISITE2_RAW_EVIDENCE_OPENAI_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        self.timeout_seconds = timeout_seconds

    def extract(self, packet: dict[str, Any]) -> dict[str, Any]:
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
                    {"role": "system", "content": _RAW_METRIC_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(packet, ensure_ascii=False, sort_keys=True),
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


class CodexOAuthRawMetricExtractionProvider:
    provider_name = "codex_oauth_raw_metric"

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
        if not (Path.home() / ".codex" / "auth.json").exists():
            raise RuntimeError("Codex OAuth auth not found. Run `codex login` first.")
        self.model = (
            model
            or os.getenv("ISITE2_RAW_EVIDENCE_CODEX_MODEL")
            or os.getenv("ISITE2_CODEX_OAUTH_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or "gpt-5.4-mini"
        )
        self.reasoning_effort = (
            reasoning_effort
            or os.getenv("ISITE2_RAW_EVIDENCE_REASONING_EFFORT")
            or os.getenv("ISITE2_CODEX_OAUTH_REASONING_EFFORT")
            or "low"
        )
        self.timeout_seconds = timeout_seconds

    def extract(self, packet: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="isite2_raw_metric_") as directory:
            temp_dir = Path(directory)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / "metric_decision.json"
            schema_path.write_text(
                json.dumps(_RAW_METRIC_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prompt = "\n\n".join(
                [
                    _RAW_METRIC_SYSTEM_PROMPT,
                    "Return only a JSON object matching the provided schema.",
                    json.dumps(packet, ensure_ascii=False, sort_keys=True),
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
                    f"Codex OAuth raw metric extraction failed with exit "
                    f"{process.returncode}: {stderr}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex OAuth raw metric extraction produced no JSON")
            decision = json.loads(output_path.read_text(encoding="utf-8"))
            decision.setdefault("provider_type", "gpt")
            decision.setdefault("provider_model", f"codex-oauth:{self.model}")
            return decision


class FileCachedRawMetricExtractionProvider:
    provider_name = "file_cached_raw_metric_gpt"

    def __init__(
        self,
        provider: RawMetricExtractionProvider,
        cache_dir: Path = DEFAULT_RAW_METRIC_CACHE_DIR,
    ) -> None:
        self.provider = provider
        self.cache_dir = cache_dir
        self.provider_name = f"cached:{provider.provider_name}"

    def extract(self, packet: dict[str, Any]) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{_cache_key(packet)}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached["cache_status"] = "hit"
            return cached
        decision = self.provider.extract(packet)
        decision["cache_status"] = "miss"
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
        return decision


def raw_metric_cleaner_from_env() -> RawEvidenceMetricCleaner | None:
    enabled = os.getenv("ISITE2_RAW_EVIDENCE_GPT_CLEANING", "").strip().casefold()
    if enabled in {"", "0", "false", "no", "off"}:
        return None
    provider_mode = os.getenv("ISITE2_RAW_EVIDENCE_GPT_PROVIDER", "codex-oauth")
    provider = raw_metric_provider_from_mode(provider_mode)
    cache_dir = (
        None
        if os.getenv("ISITE2_RAW_EVIDENCE_GPT_NO_CACHE")
        else DEFAULT_RAW_METRIC_CACHE_DIR
    )
    return RawEvidenceMetricCleaner(provider=provider, cache_dir=cache_dir)


def raw_metric_provider_from_mode(mode: str) -> RawMetricExtractionProvider:
    selected = mode.strip().casefold()
    if selected == "codex-oauth":
        return CodexOAuthRawMetricExtractionProvider()
    if selected in {"gpt", "openai"}:
        return OpenAIRawMetricExtractionProvider()
    if selected == "auto":
        if (Path.home() / ".codex" / "auth.json").exists():
            return CodexOAuthRawMetricExtractionProvider()
        return CodexOAuthRawMetricExtractionProvider()
    raise ValueError("raw metric provider mode must be one of: auto, gpt, openai, codex-oauth")


def _needs_gpt_metric_cleaning(draft: CandidateDraft, content_text: str) -> bool:
    if len(content_text.strip()) < 40:
        return False
    field_key = _field_key(draft)
    if not field_key or not str(draft.field_value or "").strip():
        return True
    if field_key not in QUANTITATIVE_PRIMARY.get(draft.scene_type, []):
        return _contains_metric_signal(content_text)
    numeric_value, _ = _numeric_value_and_unit(field_key, draft.field_value)
    if numeric_value is None and field_key == "line_count":
        p81_context = f"{draft.field_value} {content_text[:1200]}".casefold()
        if "p81" in p81_context or "wikidata" in p81_context:
            return True
    return numeric_value is None and _contains_metric_signal(
        f"{draft.field_value} {content_text[:1200]}"
    )


def _validate_gpt_metric_decision(
    decision: dict[str, Any],
    *,
    scene_type: str,
    min_confidence: float,
) -> dict[str, Any]:
    if not bool(decision.get("extracted")):
        return {"accepted": False, "reason": _reason(decision, "GPT found no metric")}
    field_group = _normalize_key(str(decision.get("field_group") or ""))
    indicator_name = _normalize_key(str(decision.get("indicator_name") or field_group))
    if field_group not in QUANTITATIVE_PRIMARY.get(scene_type, []):
        return {
            "accepted": False,
            "reason": f"GPT metric field is not a quantitative primary indicator: {field_group}",
        }
    if not bool(decision.get("is_scene_primary_metric")):
        return {"accepted": False, "reason": "GPT marked the metric as non-primary"}
    confidence = _float_or_none(decision.get("confidence"))
    if confidence is None or confidence < min_confidence:
        return {
            "accepted": False,
            "reason": f"GPT metric confidence below threshold: {confidence}",
        }
    field_value = _clean_field_value(decision.get("field_value"))
    numeric_value, unit = _numeric_value_and_unit(field_group, field_value)
    if numeric_value is None:
        return {
            "accepted": False,
            "reason": f"local numeric parser rejected GPT field_value: {field_value}",
        }
    return {
        "accepted": True,
        "field_group": field_group,
        "indicator_name": indicator_name or field_group,
        "field_value": field_value,
        "numeric_value": numeric_value,
        "unit": unit,
    }


def _metric_packet(raw: Any, draft: CandidateDraft, content_text: str) -> dict[str, Any]:
    scene_rule = get_scene_rule(draft.scene_type)
    text_hash = _text_hash(content_text)
    return {
        "schema_version": RAW_METRIC_EXTRACTION_SCHEMA_VERSION,
        "source_url": str(getattr(raw, "source_url", "") or draft.source_url),
        "source_name": str(getattr(raw, "source_name", "") or draft.source_name),
        "source_date": getattr(raw, "source_date", None) or draft.source_date,
        "text_hash": text_hash,
        "property": {
            "country": draft.country,
            "city": draft.city,
            "property_name": draft.property_name,
            "scene_type": draft.scene_type,
        },
        "current_metric": {
            "field_group": draft.field_group,
            "indicator_name": draft.indicator_name,
            "field_value": draft.field_value,
        },
        "scene_metric_contract": {
            "primary_indicators": scene_rule.get("primary_indicators", []),
            "quantitative_primary_indicators": QUANTITATIVE_PRIMARY.get(
                draft.scene_type,
                [],
            ),
        },
        "raw_text": content_text[:MAX_CONTEXT_CHARS],
        "rules": [
            "Use only raw_text and current_metric; do not browse.",
            "Extract one objective scene metric only when it is explicitly stated.",
            "Return extracted=false when the text only has a role/description.",
            "Do not parse property IDs such as Wikidata P81 as numeric values.",
            "Line labels like Line 1 or línea 1 are names, not counts, unless the text "
            "explicitly counts the connected/served lines.",
            "Years such as 2024/1995 are dates, not passenger or visitor counts.",
        ],
    }


def _raw_content_text(raw: Any, draft: CandidateDraft) -> str:
    payload = getattr(raw, "payload", None) or {}
    return str(
        draft.content_text
        or payload.get("content_text")
        or getattr(raw, "field_value", None)
        or draft.field_value
        or ""
    )


def _field_key(draft: CandidateDraft) -> str:
    for value in [draft.field_group, draft.indicator_name]:
        key = _normalize_key(str(value or ""))
        if key:
            return key
    return ""


def _contains_metric_signal(text: str) -> bool:
    lowered = text.casefold()
    if not any(char.isdigit() for char in lowered):
        return False
    metric_tokens = {
        "passenger",
        "pax",
        "visitor",
        "visits",
        "footfall",
        "capacity",
        "seat",
        "sqm",
        "m2",
        "m²",
        "square",
        "rooms",
        "keys",
        "beds",
        "students",
        "enrollment",
        "ridership",
        "line count",
        "lines",
        "routes",
    }
    return any(token in lowered for token in metric_tokens)


def _assumption_note(decision: dict[str, Any]) -> str:
    parts = [
        "GPT raw metric extraction",
        RAW_METRIC_EXTRACTION_SCHEMA_VERSION,
        f"confidence={_float_or_none(decision.get('confidence'))}",
    ]
    quote = _clean_field_value(decision.get("evidence_quote"))
    if quote:
        parts.append(f"quote: {quote[:180]}")
    explanation = _clean_field_value(decision.get("explanation"))
    if explanation:
        parts.append(f"explanation: {explanation[:180]}")
    return "; ".join(parts)


def _cache_key(packet: dict[str, Any]) -> str:
    identity = {
        "schema_version": packet.get("schema_version"),
        "source_url": packet.get("source_url"),
        "text_hash": packet.get("text_hash"),
        "property": packet.get("property"),
        "current_metric": packet.get("current_metric"),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_field_value(value: Any) -> str:
    return " ".join(str(value or "").split())[:500]


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _reason(decision: dict[str, Any], fallback: str) -> str:
    for key in ("review_reason", "explanation", "next_action"):
        value = _clean_field_value(decision.get(key))
        if value:
            return value
    return fallback


_RAW_METRIC_SYSTEM_PROMPT = """
You are the iSite2 raw evidence metric extraction agent.

Use only the provided raw_text and current_metric. Do not browse, infer hidden facts,
or convert descriptions into measurements. Your job is to clean one objective
scene metric before it reaches the candidate pool.

Return extracted=true only when the source text explicitly supports a quantitative
primary indicator for the property's scene. Return extracted=false for vague role
evidence, marketing descriptions, default values, years mistaken as metrics, or
property/database identifiers mistaken as metrics.

Critical guardrails:
- Airport gateway_role/hub_role is not a primary metric.
- Wikidata P81 is a property identifier, not line_count=81.
- A counted relationship such as "2 rail/metro lines connected per Wikidata P81
  statements" may be line_count=2.
- Line labels such as Line 1 or línea 1 are not counts unless the text explicitly
  counts connected or served lines.
- Keep evidence_quote tied to the exact source wording.
""".strip()


_RAW_METRIC_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted": {"type": "boolean"},
        "field_group": {"type": ["string", "null"]},
        "indicator_name": {"type": ["string", "null"]},
        "field_value": {"type": ["string", "null"]},
        "numeric_value": {"type": ["number", "null"]},
        "unit": {"type": ["string", "null"]},
        "is_scene_primary_metric": {"type": "boolean"},
        "confidence": {"type": "number"},
        "evidence_quote": {"type": ["string", "null"]},
        "explanation": {"type": ["string", "null"]},
        "review_reason": {"type": ["string", "null"]},
        "next_action": {"type": ["string", "null"]},
    },
    "required": [
        "extracted",
        "field_group",
        "indicator_name",
        "field_value",
        "numeric_value",
        "unit",
        "is_scene_primary_metric",
        "confidence",
        "evidence_quote",
        "explanation",
        "review_reason",
        "next_action",
    ],
    "additionalProperties": False,
}
