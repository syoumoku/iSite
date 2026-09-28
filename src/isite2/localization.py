from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine

from isite2.domain.models import SitePacket
from isite2.rules.config_loader import load_localization_config, scene_definitions

LOCALIZATION_SCHEMA_VERSION = "localization.v1"
DEFAULT_TRANSLATION_MODEL = "gpt-5.4-mini"
USABLE_LOCALIZATION_STATUSES = frozenset({"success", "translated", "completed", "ok"})


class TranslationProvider(Protocol):
    provider_name: str
    provider_model: str

    def translate(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def translate_many(self, requests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class LocalizedTextResult:
    source_text: str
    translated_text: str
    source_locale: str
    target_locale: str
    text_kind: str
    source_text_hash: str
    status: str
    provider_name: str | None = None
    provider_model: str | None = None
    confidence: float | None = None


class NoopTranslationProvider:
    provider_name = "noop_translation"
    provider_model = "noop"

    def translate(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "translated_text": request.get("source_text") or "",
            "confidence": 0.0,
            "status": "skipped_no_provider",
            "provider_model": self.provider_model,
        }

    def translate_many(self, requests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        for index, request in enumerate(requests):
            decision = self.translate(request)
            decision["index"] = index
            decisions.append(decision)
        return decisions


class CodexOAuthTranslationProvider:
    provider_name = "codex_oauth_translation"

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
            or os.getenv("ISITE2_LOCALIZATION_CODEX_MODEL")
            or os.getenv("ISITE2_CODEX_OAUTH_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or DEFAULT_TRANSLATION_MODEL
        )
        self.reasoning_effort = (
            reasoning_effort
            or os.getenv("ISITE2_LOCALIZATION_REASONING_EFFORT")
            or os.getenv("ISITE2_CODEX_OAUTH_REASONING_EFFORT")
            or "low"
        )
        self.timeout_seconds = _env_float("ISITE2_LOCALIZATION_TIMEOUT_SECONDS", timeout_seconds)
        self.provider_model = f"codex-oauth:{self.model}"

    def translate(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = self._run_codex_json(
            request=request,
            schema=_TRANSLATION_OUTPUT_SCHEMA,
            output_filename="translation.json",
            instruction="Return only a JSON object matching the provided schema.",
        )
        payload.setdefault("provider_model", self.provider_model)
        payload.setdefault("status", "success")
        return payload

    def translate_many(self, requests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed_requests = []
        for index, request in enumerate(requests):
            item = dict(request)
            item["index"] = index
            item.pop("requirements", None)
            indexed_requests.append(item)
        payload = self._run_codex_json(
            request={
                "items": indexed_requests,
                "requirements": [
                    "Translate every item independently into its target_locale.",
                    "Return exactly one translation object per input item.",
                    "Preserve each input index unchanged.",
                    (
                        "A mixed-language item is not already in the target language. "
                        "Translate every translatable embedded phrase into target_locale."
                    ),
                    (
                        "Preserve numbers, units, years, URLs, source names, "
                        "and original property names."
                    ),
                    (
                        "Translate surrounding prose; do not leave English "
                        "explanation phrases in zh output."
                    ),
                    (
                        "For each zh item, translate or remove every ordinary-language "
                        "token listed in translatable_latin_tokens. Preserve it only "
                        "when it is part of the item's original property/source name."
                    ),
                    "Do not add facts or rewrite objective evidence.",
                ],
            },
            schema=_TRANSLATION_BATCH_OUTPUT_SCHEMA,
            output_filename="translations.json",
            instruction=(
                "Return only a JSON object matching the provided schema. "
                "Do not omit any input item."
            ),
        )
        decisions = payload.get("translations") or []
        if not isinstance(decisions, list):
            raise RuntimeError("Codex OAuth localization batch returned no translations list")
        normalized: list[dict[str, Any]] = []
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            decision.setdefault("provider_model", self.provider_model)
            decision.setdefault("status", "success")
            normalized.append(decision)
        return normalized

    def _run_codex_json(
        self,
        *,
        request: dict[str, Any],
        schema: dict[str, Any],
        output_filename: str,
        instruction: str,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="isite2_localization_") as directory:
            temp_dir = Path(directory)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / output_filename
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prompt = "\n\n".join(
                [
                    _TRANSLATION_SYSTEM_PROMPT,
                    instruction,
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
                    f"Codex OAuth localization failed with exit {process.returncode}: {stderr}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex OAuth localization produced no JSON")
            return json.loads(output_path.read_text(encoding="utf-8"))


class DatabaseLocalizationCache:
    def __init__(self, engine: Engine, *, ensure_schema: bool = True) -> None:
        self.engine = engine
        if ensure_schema:
            ensure_localized_text_cache_schema(engine)

    def lookup(
        self,
        *,
        source_text: str,
        text_kind: str,
        target_locale: str,
        source_locale: str | None = None,
        schema_version: str | None = None,
        usable_only: bool = True,
    ) -> LocalizedTextResult | None:
        source_hash = source_text_hash(source_text)
        legacy_hash = _legacy_source_text_hash(source_text)
        source_locale = source_locale or detect_source_locale(source_text)
        legacy_source_locale = (
            "mixed" if source_locale in {"en", "zh"} else source_locale
        )
        schema_version = schema_version or localization_schema_version()
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT source_text_hash, text_kind, source_locale, target_locale,
                           source_text, translated_text, provider_name, provider_model,
                           confidence, status
                    FROM localized_text_cache
                    WHERE source_text_hash IN (:source_text_hash, :legacy_source_text_hash)
                      AND text_kind = :text_kind
                      AND source_locale IN (:source_locale, :legacy_source_locale)
                      AND target_locale = :target_locale
                      AND schema_version = :schema_version
                    ORDER BY CASE WHEN source_text_hash = :source_text_hash THEN 0 ELSE 1 END,
                             CASE WHEN source_locale = :source_locale THEN 0 ELSE 1 END
                    LIMIT 1
                    """
                    ),
                    {
                        "source_text_hash": source_hash,
                        "legacy_source_text_hash": legacy_hash,
                        "text_kind": text_kind,
                        "source_locale": source_locale,
                        "legacy_source_locale": legacy_source_locale,
                        "target_locale": target_locale,
                        "schema_version": schema_version,
                    },
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        result = LocalizedTextResult(
            source_text=str(row["source_text"] or ""),
            translated_text=str(row["translated_text"] or ""),
            source_locale=str(row["source_locale"] or ""),
            target_locale=str(row["target_locale"] or ""),
            text_kind=str(row["text_kind"] or ""),
            source_text_hash=str(row["source_text_hash"] or ""),
            status=str(row["status"] or "success"),
            provider_name=row["provider_name"],
            provider_model=row["provider_model"],
            confidence=row["confidence"],
        )
        if usable_only and not is_usable_localized_text_result(result):
            return None
        return result

    def load_results(
        self,
        *,
        schema_version: str | None = None,
    ) -> list[LocalizedTextResult]:
        resolved_schema_version = schema_version or localization_schema_version()
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                        SELECT source_text_hash, text_kind, source_locale, target_locale,
                               source_text, translated_text, provider_name, provider_model,
                               confidence, status
                        FROM localized_text_cache
                        WHERE schema_version = :schema_version
                        ORDER BY updated_at DESC
                        """
                    ),
                    {"schema_version": resolved_schema_version},
                )
                .mappings()
                .all()
            )
        return [
            LocalizedTextResult(
                source_text=str(row["source_text"] or ""),
                translated_text=str(row["translated_text"] or ""),
                source_locale=str(row["source_locale"] or ""),
                target_locale=str(row["target_locale"] or ""),
                text_kind=str(row["text_kind"] or ""),
                source_text_hash=str(row["source_text_hash"] or ""),
                status=str(row["status"] or "success"),
                provider_name=row["provider_name"],
                provider_model=row["provider_model"],
                confidence=row["confidence"],
            )
            for row in rows
        ]

    def lookup_reusable(
        self,
        *,
        source_text: str,
        target_locale: str,
        source_locale: str | None = None,
        schema_version: str | None = None,
    ) -> LocalizedTextResult | None:
        source_hash = source_text_hash(source_text)
        legacy_hash = _legacy_source_text_hash(source_text)
        source_locale = source_locale or detect_source_locale(source_text)
        legacy_source_locale = (
            "mixed" if source_locale in {"en", "zh"} else source_locale
        )
        schema_version = schema_version or localization_schema_version()
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT source_text_hash, text_kind, source_locale, target_locale,
                           source_text, translated_text, provider_name, provider_model,
                           confidence, status
                    FROM localized_text_cache
                    WHERE source_text_hash IN (:source_text_hash, :legacy_source_text_hash)
                      AND source_locale IN (:source_locale, :legacy_source_locale)
                      AND target_locale = :target_locale
                      AND schema_version = :schema_version
                    ORDER BY CASE WHEN source_text_hash = :source_text_hash THEN 0 ELSE 1 END,
                             CASE WHEN source_locale = :source_locale THEN 0 ELSE 1 END,
                             CASE WHEN status IN (
                               'success', 'translated', 'completed', 'ok'
                             ) THEN 0 ELSE 1 END,
                             updated_at DESC
                    """
                    ),
                    {
                        "source_text_hash": source_hash,
                        "legacy_source_text_hash": legacy_hash,
                        "source_locale": source_locale,
                        "legacy_source_locale": legacy_source_locale,
                        "target_locale": target_locale,
                        "schema_version": schema_version,
                    },
                )
                .mappings()
                .all()
            )
        for row in rows:
            result = LocalizedTextResult(
                source_text=str(row["source_text"] or ""),
                translated_text=str(row["translated_text"] or ""),
                source_locale=str(row["source_locale"] or ""),
                target_locale=str(row["target_locale"] or ""),
                text_kind=str(row["text_kind"] or ""),
                source_text_hash=str(row["source_text_hash"] or ""),
                status=str(row["status"] or "success"),
                provider_name=row["provider_name"],
                provider_model=row["provider_model"],
                confidence=row["confidence"],
            )
            if is_usable_localized_text_result(result):
                return result
        return None

    def upsert(
        self,
        *,
        source_text: str,
        translated_text: str,
        text_kind: str,
        target_locale: str,
        source_locale: str | None = None,
        schema_version: str | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        confidence: float | None = None,
        status: str = "success",
    ) -> LocalizedTextResult:
        source_hash = source_text_hash(source_text)
        source_locale = source_locale or detect_source_locale(source_text)
        schema_version = schema_version or localization_schema_version()
        row_id = _cache_row_id(
            source_hash=source_hash,
            text_kind=text_kind,
            source_locale=source_locale,
            target_locale=target_locale,
            schema_version=schema_version,
        )
        params = {
            "id": row_id,
            "source_text_hash": source_hash,
            "text_kind": text_kind,
            "source_locale": source_locale,
            "target_locale": target_locale,
            "schema_version": schema_version,
            "source_text": source_text[:4096],
            "translated_text": translated_text,
            "provider_name": provider_name,
            "provider_model": provider_model,
            "confidence": confidence,
            "status": status,
        }
        with self.engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE localized_text_cache
                    SET translated_text = :translated_text,
                        provider_name = :provider_name,
                        provider_model = :provider_model,
                        confidence = :confidence,
                        status = :status,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE source_text_hash = :source_text_hash
                      AND text_kind = :text_kind
                      AND source_locale = :source_locale
                      AND target_locale = :target_locale
                      AND schema_version = :schema_version
                    """
                ),
                params,
            ).rowcount
            if not updated:
                connection.execute(
                    text(
                        """
                        INSERT INTO localized_text_cache (
                          id, source_text_hash, text_kind, source_locale, target_locale,
                          schema_version, source_text, translated_text, provider_name,
                          provider_model, confidence, status, created_at, updated_at
                        ) VALUES (
                          :id, :source_text_hash, :text_kind, :source_locale, :target_locale,
                          :schema_version, :source_text, :translated_text, :provider_name,
                          :provider_model, :confidence, :status, CURRENT_TIMESTAMP,
                          CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    params,
                )
        return LocalizedTextResult(
            source_text=source_text,
            translated_text=translated_text,
            source_locale=source_locale,
            target_locale=target_locale,
            text_kind=text_kind,
            source_text_hash=source_hash,
            status=status,
            provider_name=provider_name,
            provider_model=provider_model,
            confidence=confidence,
        )


def ensure_localized_text_cache_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS localized_text_cache (
                  id TEXT PRIMARY KEY,
                  source_text_hash TEXT NOT NULL,
                  text_kind TEXT NOT NULL,
                  source_locale TEXT NOT NULL,
                  target_locale TEXT NOT NULL,
                  schema_version TEXT NOT NULL,
                  source_text TEXT NOT NULL,
                  translated_text TEXT NOT NULL,
                  provider_name TEXT,
                  provider_model TEXT,
                  confidence DOUBLE PRECISION,
                  status TEXT NOT NULL DEFAULT 'success',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE (
                    source_text_hash, text_kind, source_locale, target_locale, schema_version
                  )
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_localized_text_cache_lookup
                ON localized_text_cache (
                  target_locale, text_kind, source_text_hash, schema_version
                )
                """
            )
        )


def resolve_locale(locale: str | None = None) -> str:
    config = load_localization_config()
    supported = set(config.get("supported_locales") or [])
    default = str(config.get("default_locale") or "en")
    value = str(locale or default).strip().replace("_", "-").casefold()
    if value in supported:
        return value
    short = value.split("-", 1)[0]
    if short in supported:
        return short
    return default


def supported_locales() -> list[str]:
    return list(load_localization_config().get("supported_locales") or ["en"])


def default_locale() -> str:
    return resolve_locale(str(load_localization_config().get("default_locale") or "en"))


def localization_schema_version() -> str:
    return str(load_localization_config().get("schema_version") or LOCALIZATION_SCHEMA_VERSION)


def localization_payload(locale: str | None = None) -> dict[str, Any]:
    resolved = resolve_locale(locale)
    config = load_localization_config()
    return {
        "version": config.get("version"),
        "schema_version": localization_schema_version(),
        "locale": resolved,
        "default_locale": default_locale(),
        "fallback_locale": str(config.get("fallback_locale") or "en"),
        "supported_locales": supported_locales(),
        "entity_name_policy": config.get("entity_name_policy"),
        "evidence_text_policy": config.get("evidence_text_policy"),
        "labels": _locale_labels(resolved),
        "fallback_labels": _locale_labels(str(config.get("fallback_locale") or "en")),
    }


def t(key: str, locale: str | None = None, default: str | None = None, **kwargs: Any) -> str:
    resolved = resolve_locale(locale)
    value = _lookup_label(resolved, key)
    if value is None:
        fallback = str(load_localization_config().get("fallback_locale") or "en")
        value = _lookup_label(fallback, key)
    if value is None:
        value = default if default is not None else key
    text_value = str(value)
    return _format_text(text_value, kwargs)


def scene_label(scene_type: str | None, locale: str | None = None) -> str:
    value = str(scene_type or "")
    if not value:
        return t("fallback.unknown", locale)
    return t(f"scenes.{value}", locale, default=value.replace("_", " "))


def metric_label(metric_key: str | None, locale: str | None = None) -> str:
    value = str(metric_key or "")
    if not value:
        return t("fallback.unknown", locale)
    return t(f"metrics.{value}", locale, default=value.replace("_", " "))


def enum_label(value: Any, locale: str | None = None) -> str:
    raw = _raw_value(value)
    if not raw:
        return t("fallback.unknown", locale)
    return t(f"enums.{raw}", locale, default=raw)


def excel_labels(locale: str | None = None) -> dict[str, Any]:
    labels = _locale_labels(resolve_locale(locale))
    return dict(labels.get("excel") or {})


def ppt_text(key: str, locale: str | None = None, default: str | None = None, **kwargs: Any) -> str:
    return t(f"ppt.{key}", locale, default=default, **kwargs)


def ui_text(key: str, locale: str | None = None, default: str | None = None, **kwargs: Any) -> str:
    return t(f"ui.{key}", locale, default=default, **kwargs)


def localize_text(
    source_text: str | None,
    *,
    text_kind: str,
    target_locale: str | None = None,
    source_locale: str | None = None,
    cache: DatabaseLocalizationCache | None = None,
    provider: TranslationProvider | None = None,
    allow_provider: bool = False,
    fallback_text: str | None = None,
) -> LocalizedTextResult:
    text_value = str(source_text or "")
    resolved = resolve_locale(target_locale)
    detected_source_locale = source_locale or detect_source_locale(text_value)
    if not text_value:
        return LocalizedTextResult(
            source_text="",
            translated_text="",
            source_locale=detected_source_locale,
            target_locale=resolved,
            text_kind=text_kind,
            source_text_hash=source_text_hash(""),
            status="empty",
        )
    if detected_source_locale == resolved:
        return LocalizedTextResult(
            source_text=text_value,
            translated_text=text_value,
            source_locale=detected_source_locale,
            target_locale=resolved,
            text_kind=text_kind,
            source_text_hash=source_text_hash(text_value),
            status="same_locale",
        )
    cached = (
        cache.lookup(
            source_text=text_value,
            text_kind=text_kind,
            source_locale=detected_source_locale,
            target_locale=resolved,
        )
        if cache is not None
        else None
    )
    if cached is not None:
        return cached
    if not allow_provider or provider is None:
        translated = (
            fallback_text if fallback_text is not None else localization_pending_text(resolved)
        )
        return LocalizedTextResult(
            source_text=text_value,
            translated_text=translated,
            source_locale=detected_source_locale,
            target_locale=resolved,
            text_kind=text_kind,
            source_text_hash=source_text_hash(text_value),
            status="cache_miss",
        )
    request = {
        "source_text": text_value,
        "text_kind": text_kind,
        "source_locale": detected_source_locale,
        "target_locale": resolved,
        "requirements": [
            "Preserve numbers, units, years, URLs, source names, and original property names.",
            "Translate surrounding prose; do not leave English explanation phrases in zh output.",
            "Do not add facts or rewrite objective evidence.",
            "Keep the translation concise and auditable.",
        ],
    }
    decision = provider.translate(request)
    translated = apply_localization_glossary(
        str(decision.get("translated_text") or text_value),
        resolved,
    )
    confidence = _optional_float(decision.get("confidence"))
    status = str(decision.get("status") or "success")
    if not is_usable_translation(
        source_text=text_value,
        translated_text=translated,
        source_locale=detected_source_locale,
        target_locale=resolved,
        status=status,
    ):
        raise RuntimeError(
            "translation provider returned an unusable cross-locale result "
            f"(status={status})"
        )
    if cache is not None:
        return cache.upsert(
            source_text=text_value,
            translated_text=translated,
            text_kind=text_kind,
            source_locale=detected_source_locale,
            target_locale=resolved,
            provider_name=provider.provider_name,
            provider_model=str(decision.get("provider_model") or provider.provider_model),
            confidence=confidence,
            status=status,
        )
    return LocalizedTextResult(
        source_text=text_value,
        translated_text=translated,
        source_locale=detected_source_locale,
        target_locale=resolved,
        text_kind=text_kind,
        source_text_hash=source_text_hash(text_value),
        status=status,
        provider_name=provider.provider_name,
        provider_model=str(decision.get("provider_model") or provider.provider_model),
        confidence=confidence,
    )


def localize_packet(
    packet: SitePacket,
    *,
    locale: str | None = None,
    cache: DatabaseLocalizationCache | None = None,
    provider: TranslationProvider | None = None,
    allow_provider: bool = False,
) -> dict[str, Any]:
    resolved = resolve_locale(locale)
    conclusion = packet.conclusion
    primary_metric_result = _localized_primary_metric(
        packet, resolved, cache, provider, allow_provider
    )
    reason_result = _localized_free_text_result(
        conclusion.reason_to_recommend,
        "reason_to_recommend",
        resolved,
        cache,
        provider,
        allow_provider,
    )
    next_action_result = _localized_free_text_result(
        conclusion.next_action,
        "next_action",
        resolved,
        cache,
        provider,
        allow_provider,
    )
    return {
        "locale": resolved,
        "schema_version": localization_schema_version(),
        "entity": {
            "display_name": packet.entity.property_name,
            "original_name": packet.entity.property_name,
            "scene_label": scene_label(packet.entity.scene_type, resolved),
        },
        "scene": {
            "scene_type_label": scene_label(packet.entity.scene_type, resolved),
            "area_metric_label": metric_label(packet.scene.area_metric_name, resolved),
            "proxy_level_label": enum_label(packet.scene.proxy_level, resolved),
        },
        "primary_metric": primary_metric_result,
        "build_status": {
            "indoor_system_presence_label": enum_label(
                packet.build_status.indoor_system_presence, resolved
            ),
            "indoor_system_type_label": enum_label(
                packet.build_status.indoor_system_type, resolved
            ),
            "indoor_rat_label": enum_label(packet.build_status.indoor_rat, resolved),
            "build_evidence_status_label": enum_label(
                packet.build_status.build_evidence_status, resolved
            ),
        },
        "conclusion": {
            "evidence_status_label": enum_label(conclusion.evidence_status, resolved),
            "value_class_label": enum_label(conclusion.value_class, resolved),
            "action_class_label": enum_label(conclusion.action_class, resolved),
            "recommended_solution_label": enum_label(conclusion.recommended_solution, resolved),
            "reason_to_recommend": reason_result.translated_text,
            "reason_to_recommend_original": conclusion.reason_to_recommend,
            "reason_to_recommend_localization_status": reason_result.status,
            "reason_to_recommend_source_locale": reason_result.source_locale,
            "next_action": next_action_result.translated_text,
            "next_action_original": conclusion.next_action,
            "next_action_localization_status": next_action_result.status,
            "next_action_source_locale": next_action_result.source_locale,
        },
        "evidence": [
            {
                "field_group_label": metric_label(item.field_group, resolved),
                "indicator_label": metric_label(item.indicator_name or item.field_group, resolved),
                "field_value": _localized_free_text(
                    item.field_value,
                    "evidence.field_value",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "field_value_original": item.field_value,
                "evidence_type_label": enum_label(item.evidence_type, resolved),
                "source_tier_label": enum_label(item.source_tier, resolved),
                "cross_check_status_label": enum_label(item.cross_check_status, resolved),
            }
            for item in packet.evidence
        ],
        "inference": [
            {
                "inferred_field_label": metric_label(item.inferred_field, resolved),
                "inferred_value": _localized_free_text(
                    item.inferred_value,
                    "inference.inferred_value",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "inferred_value_original": item.inferred_value,
                "inference_basis": _localized_free_text(
                    item.inference_basis,
                    "inference.inference_basis",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "inference_basis_original": item.inference_basis,
                "inference_chain": _localized_free_text(
                    item.inference_chain,
                    "inference.inference_chain",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "inference_chain_original": item.inference_chain,
                "inference_confidence_label": enum_label(item.inference_confidence, resolved),
            }
            for item in packet.inference
        ],
        "review_queue": [
            {
                "reason": _localized_free_text(
                    item.reason,
                    "review.reason",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "reason_original": item.reason,
                "next_action": _localized_free_text(
                    item.next_action,
                    "review.next_action",
                    resolved,
                    cache,
                    provider,
                    allow_provider,
                ),
                "next_action_original": item.next_action,
                "status_label": enum_label(item.status, resolved),
            }
            for item in packet.review_queue
        ],
    }


def source_text_hash(source_text: str) -> str:
    normalized = normalize_localization_source_text(source_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_localization_source_text(source_text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(source_text or ""))
    return " ".join(normalized.split())


def is_usable_localized_text_result(result: LocalizedTextResult) -> bool:
    if result.status.casefold() not in USABLE_LOCALIZATION_STATUSES:
        return False
    if not result.translated_text.strip():
        return False
    if result.source_locale == result.target_locale:
        return True
    if normalize_localization_source_text(
        result.translated_text
    ) != normalize_localization_source_text(result.source_text):
        return _translation_language_is_aligned(
            result.source_text,
            result.translated_text,
            result.source_locale,
            result.target_locale,
        )
    return not _contains_language_bearing_text(result.source_text, result.target_locale)


def is_usable_translation(
    *,
    source_text: str,
    translated_text: str,
    source_locale: str,
    target_locale: str,
    status: str,
) -> bool:
    return is_usable_localized_text_result(
        LocalizedTextResult(
            source_text=source_text,
            translated_text=translated_text,
            source_locale=source_locale,
            target_locale=target_locale,
            text_kind="validation",
            source_text_hash=source_text_hash(source_text),
            status=status,
        )
    )


def _contains_language_bearing_text(source_text: str, target_locale: str) -> bool:
    if target_locale == "en":
        return any("\u4e00" <= char <= "\u9fff" for char in source_text)
    if target_locale == "zh":
        return _contains_translatable_lowercase_latin(source_text)
    return True


def _translation_language_is_aligned(
    source_text: str,
    translated_text: str,
    source_locale: str,
    target_locale: str,
) -> bool:
    source_cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in source_text)
    translated_cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in translated_text)
    source_latin_count = len(re.findall(r"[A-Za-z]{3,}", source_text))
    translated_latin_count = len(re.findall(r"[A-Za-z]{3,}", translated_text))
    if target_locale == "zh" and source_locale in {"en", "mixed"}:
        if source_latin_count == 0:
            return True
        return (
            translated_latin_count < source_latin_count
            or translated_cjk_count > source_cjk_count
        )
    if target_locale == "en" and source_locale in {"zh", "mixed"}:
        if source_cjk_count == 0:
            return True
        return (
            translated_cjk_count < source_cjk_count
            or translated_latin_count > source_latin_count
        )
    return True


def _legacy_source_text_hash(source_text: str) -> str:
    return hashlib.sha256(str(source_text or "").encode("utf-8")).hexdigest()


def detect_source_locale(source_text: str) -> str:
    cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in source_text)
    latin_words = re.findall(r"[A-Za-z]{2,}", source_text)
    latin_letter_count = sum(len(word) for word in latin_words)
    if cjk_count and latin_letter_count:
        if cjk_count >= 4 and _contains_chinese_prose_marker(source_text):
            suffix_after_name = source_text.rsplit("的", 1)[-1]
            if "的" in source_text and not re.search(r"[A-Za-z]{2,}", suffix_after_name):
                return "zh"
            return (
                "mixed"
                if _contains_translatable_lowercase_latin(source_text)
                else "zh"
            )
        if cjk_count >= max(12, 4 * len(latin_words)):
            return "zh"
        if latin_letter_count >= max(24, 4 * cjk_count):
            return "en"
        return "mixed"
    if cjk_count:
        return "zh"
    return "en"


def _contains_chinese_prose_marker(source_text: str) -> bool:
    return bool(
        re.search(
            r"(补查|补采|补充|核验|复核|推荐|优先|确认|进入|主指标|下一步|"
            r"来源|年度|客流|方案|依据|该物业|该场馆|该机场|该校区)",
            source_text,
        )
    )


def _contains_translatable_lowercase_latin(source_text: str) -> bool:
    return bool(translatable_latin_tokens(source_text))


def translatable_latin_tokens(source_text: str) -> list[str]:
    without_metric_keys = re.sub(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
        " ",
        source_text,
    )
    matches = _latin_word_matches(without_metric_keys)
    proper_name_indexes = _proper_name_phrase_token_indexes(
        without_metric_keys,
        matches,
    )
    tokens: list[str] = []
    for index, match in enumerate(matches):
        token = match.group(0)
        if len(token) < 3:
            continue
        if index in proper_name_indexes:
            continue
        if token.casefold() in _LOCALE_NEUTRAL_LOWERCASE_TOKENS:
            continue
        if token.isupper() or token.istitle():
            continue
        if any(char.isupper() for char in token[1:]):
            continue
        if token.casefold() in _PROPER_NAME_CONNECTORS:
            previous = matches[index - 1].group(0) if index > 0 else ""
            following = matches[index + 1].group(0) if index + 1 < len(matches) else ""
            if _looks_like_name_token(previous) and _looks_like_name_token(following):
                continue
        if token.casefold() not in {value.casefold() for value in tokens}:
            tokens.append(token)
    return tokens


def apply_localization_glossary(text_value: str, target_locale: str) -> str:
    if target_locale != "zh":
        return text_value
    translated = text_value
    for source, replacement in _ZH_DISPLAY_TERM_GLOSSARY:
        translated = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])",
            replacement,
            translated,
            flags=re.IGNORECASE,
        )
    return translated


def _proper_name_phrase_token_indexes(
    source_text: str,
    matches: list[re.Match[str]],
) -> set[int]:
    protected: set[int] = set()
    run_start = 0
    for index in range(1, len(matches) + 1):
        at_end = index == len(matches)
        separator = (
            ""
            if at_end
            else source_text[matches[index - 1].end() : matches[index].start()]
        )
        if not at_end and re.fullmatch(r"[\s'’.-]*", separator):
            continue
        run = matches[run_start:index]
        if run:
            tail = source_text[run[-1].end() :]
            next_character = next((char for char in tail if not char.isspace()), "")
            values = [match.group(0) for match in run]
            has_name_shape = any(_looks_like_name_token(value) for value in values)
            has_sentence_word = any(
                value.casefold() in _NAME_PHRASE_SENTENCE_WORDS for value in values
            )
            if (
                next_character
                and "\u4e00" <= next_character <= "\u9fff"
                and has_name_shape
                and not has_sentence_word
            ):
                protected.update(range(run_start, index))
        run_start = index
    return protected


def _latin_word_matches(source_text: str) -> list[re.Match[str]]:
    matches = re.finditer(r"[^\W\d_]+(?:-[^\W\d_]+)*", source_text, re.UNICODE)
    return [
        match
        for match in matches
        if all(
            char == "-"
            or not char.isalpha()
            or "LATIN" in unicodedata.name(char, "")
            for char in match.group(0)
        )
    ]


def _looks_like_name_token(token: str) -> bool:
    return bool(
        token
        and (
            token.isupper()
            or token.istitle()
            or any(char.isupper() for char in token[1:])
        )
    )


_PROPER_NAME_CONNECTORS = frozenset(
    {
        "and",
        "da",
        "de",
        "del",
        "des",
        "do",
        "dos",
        "du",
        "et",
        "la",
        "le",
        "of",
        "the",
    }
)

_LOCALE_NEUTRAL_LOWERCASE_TOKENS = frozenset(
    {
        "gfa",
        "gla",
        "nla",
        "sqm",
    }
)

_NAME_PHRASE_SENTENCE_WORDS = frozenset(
    {
        "are",
        "for",
        "from",
        "had",
        "has",
        "have",
        "is",
        "lists",
        "located",
        "provides",
        "reports",
        "served",
        "serves",
        "states",
        "supports",
        "was",
        "were",
        "with",
    }
)

_ZH_DISPLAY_TERM_GLOSSARY = (
    ("enrollment", "在校生数"),
    ("footfall", "客流量"),
    ("keys", "客房数"),
    ("scene", "场景"),
    ("sqft", "平方英尺"),
    ("null", "暂无"),
)


def translation_provider_from_mode(mode: str | None = None) -> TranslationProvider:
    normalized = str(mode or os.getenv("ISITE2_LOCALIZATION_PROVIDER", "codex-oauth")).casefold()
    if normalized in {"noop", "none", "off"}:
        return NoopTranslationProvider()
    if normalized in {"codex-oauth", "oauth", "auto"}:
        return CodexOAuthTranslationProvider()
    raise ValueError("localization provider mode must be one of: codex-oauth, auto, noop")


def localization_pending_text(locale: str | None = None) -> str:
    resolved = resolve_locale(locale)
    default = "本地化待刷新" if resolved == "zh" else "Localization pending"
    return t("fallback.localization_pending", resolved, default=default)


def _localized_free_text(
    source_text: str | None,
    text_kind: str,
    locale: str,
    cache: DatabaseLocalizationCache | None,
    provider: TranslationProvider | None,
    allow_provider: bool,
    fallback_text: str | None = None,
) -> str:
    return _localized_free_text_result(
        source_text,
        text_kind,
        locale,
        cache,
        provider,
        allow_provider,
        fallback_text=fallback_text,
    ).translated_text


def _localized_free_text_result(
    source_text: str | None,
    text_kind: str,
    locale: str,
    cache: DatabaseLocalizationCache | None,
    provider: TranslationProvider | None,
    allow_provider: bool,
    *,
    fallback_text: str | None = None,
) -> LocalizedTextResult:
    return localize_text(
        source_text,
        text_kind=text_kind,
        target_locale=locale,
        cache=cache,
        provider=provider,
        allow_provider=allow_provider,
        fallback_text=fallback_text,
    )


def generic_free_text_fallback(text_kind: str, locale: str | None = None) -> str | None:
    if text_kind:
        return localization_pending_text(locale)
    return None


def _localized_primary_metric(
    packet: SitePacket,
    locale: str,
    cache: DatabaseLocalizationCache | None,
    provider: TranslationProvider | None,
    allow_provider: bool,
) -> dict[str, Any]:
    evidence = _select_primary_metric_evidence(packet)
    if evidence is None:
        return {
            "display_text": t("fallback.missing_primary_metric", locale),
            "original_text": "",
            "localization_status": "missing",
            "source_locale": "",
            "field_group": "",
            "indicator_name": "",
        }
    source_text = primary_metric_display_source_text(packet)
    result = localize_text(
        source_text,
        text_kind="primary_metric.display_text",
        target_locale=locale,
        cache=cache,
        provider=provider,
        allow_provider=allow_provider,
    )
    return {
        "display_text": result.translated_text,
        "original_text": source_text,
        "localization_status": result.status,
        "source_locale": result.source_locale,
        "field_group": evidence.field_group,
        "indicator_name": evidence.indicator_name,
    }


def primary_metric_display_source_text(packet: SitePacket) -> str:
    evidence = _select_primary_metric_evidence(packet)
    if evidence is None:
        return ""
    metric_key = evidence.indicator_name or evidence.field_group
    return f"{metric_label(metric_key, 'en')}: {evidence.field_value}"


def _select_primary_metric_evidence(packet: SitePacket):
    if not packet.evidence:
        return None
    scene_type = packet.entity.scene_type
    primary_indicators = scene_definitions().get(scene_type, {}).get("primary_indicators", []) or []
    metric_order = {str(metric): index for index, metric in enumerate(primary_indicators)}

    eligible = []
    for index, item in enumerate(packet.evidence):
        metric_key = str(item.indicator_name or item.field_group or "")
        field_value = str(item.field_value or "")
        descriptive = metric_key.endswith("_role") or metric_key in {
            "gateway_role",
            "hub_role",
            "main_venue_role",
            "landmark_role",
        }
        if metric_key in metric_order and re.search(r"\d", field_value) and not descriptive:
            eligible.append((index, item))
    if not eligible:
        return None

    def priority(item_with_index) -> tuple[int, int]:
        index, item = item_with_index
        metric_key = str(item.indicator_name or item.field_group or "")
        return (metric_order[metric_key], index)

    return min(eligible, key=priority)[1]


def _locale_labels(locale: str) -> dict[str, Any]:
    config = load_localization_config()
    labels = config.get("labels") or {}
    resolved = resolve_locale(locale)
    return dict(labels.get(resolved) or labels.get(config.get("fallback_locale")) or {})


def _lookup_label(locale: str, key: str) -> Any:
    current: Any = _locale_labels(locale)
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _raw_value(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _format_text(value: str, kwargs: dict[str, Any]) -> str:
    if not kwargs:
        return value
    try:
        return value.format(**kwargs)
    except (KeyError, ValueError):
        return value


def _cache_row_id(
    *,
    source_hash: str,
    text_kind: str,
    source_locale: str,
    target_locale: str,
    schema_version: str,
) -> str:
    return hashlib.sha256(
        "|".join([source_hash, text_kind, source_locale, target_locale, schema_version]).encode(
            "utf-8"
        )
    ).hexdigest()


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _codex_oauth_available() -> bool:
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return False
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("tokens") or payload.get("OPENAI_API_KEY") or payload.get("api_key"))


_TRANSLATION_SYSTEM_PROMPT = """You translate iSite2 display text for an evidence-based
property scanning product.

Rules:
- Translate only the supplied text into the requested target locale.
- Preserve numbers, years, URLs, source names, property names, canonical snake_case
  metric keys, technical acronyms, and standard SI unit abbreviations.
- Translate surrounding prose; keep only source names, URLs, property names,
  canonical snake_case metric keys, technical acronyms, standard SI unit
  abbreviations, numbers, and years unchanged.
- A mixed-language source is not already in the target language. Translate all
  embedded ordinary-language evidence phrases, labels, and connective words.
- For zh output, translate lowercase English prose and UI terms such as indoor,
  coverage, review, queue, recommendation, seats, capacity, statement, passenger,
  room, building, and floor area. Preserve proper names and technical acronyms
  such as pRRU, hRRU, MICE, GLA, and NLA.
- English industry labels and spelled-out units are not protected identifiers.
  For zh output, translate labels such as keys/rooms, seats, retail, office,
  ballroom, enrollment/students, footfall, square meters, and square feet while
  preserving their numeric values. Standard SI unit abbreviations such as sqm,
  m2, m², GLA, NLA, and GFA may remain unchanged.
- For en output, translate Chinese prose while preserving Chinese property names
  only when they are part of the official entity name.
- Do not add new facts, explanations, qualifiers, or recommendations.
- Return the source unchanged only when source_locale exactly equals target_locale;
  never use this shortcut when source_locale is mixed.
- Keep the result concise and audit-friendly.
"""

_TRANSLATION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "translated_text": {"type": "string"},
        "confidence": {"type": "number"},
        "status": {"type": "string"},
        "provider_model": {"type": "string"},
    },
    "required": ["translated_text", "confidence", "status", "provider_model"],
}

_TRANSLATION_BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "translated_text": {"type": "string"},
                    "confidence": {"type": "number"},
                    "status": {"type": "string"},
                    "provider_model": {"type": "string"},
                },
                "required": [
                    "index",
                    "translated_text",
                    "confidence",
                    "status",
                    "provider_model",
                ],
            },
        }
    },
    "required": ["translations"],
}
