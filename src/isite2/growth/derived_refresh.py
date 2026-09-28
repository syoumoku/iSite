from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from isite2.domain.enums import (
    ActionClass,
    EvidenceStatus,
    ProxyLevel,
    RecommendedSolution,
    ValueClass,
)
from isite2.rules.config_loader import get_scene_rule, scene_definitions
from isite2.rules.demand import calculate_demand, demand_params_from_scene_rule
from isite2.rules.metric_safety import (
    is_year_like_annual_visit,
    safe_annual_visit_estimate,
)
from isite2.rules.reason import short_reason

LOW_EVIDENCE_LABEL = "low_primary_metric_evidence"
HARD_EVIDENCE_LABEL = "hard_primary_metric_available"
REVIEW_TYPE = "gpt_derived_info_refresh"
DEFAULT_CACHE_DIR = Path("outputs") / "derived_refresh" / "gpt_cache"

QUANTITATIVE_PRIMARY: dict[str, list[str]] = {
    "airport_terminal": [
        "annual_passenger_throughput",
        "passenger_throughput",
        "terminal_capacity",
        "international_passenger_share",
    ],
    "convention_center": [
        "exhibition_area",
        "meeting_area",
        "annual_events",
        "international_event_frequency",
        "peak_event_capacity",
        "plenary_capacity",
    ],
    "stadium": ["seat_count", "event_days", "international_events", "peak_event_capacity"],
    "luxury_hotel_mice": ["keys", "rooms", "meeting_ballroom_area", "ballroom_capacity"],
    "mall_mixed_use": ["annual_footfall", "footfall", "gla", "retail_gfa"],
    "office_government": [
        "office_nla",
        "office_gfa",
        "floor_count",
        "tower_height",
        "building_grade",
    ],
    "hospital": ["beds", "outpatient_volume", "staff_count", "employee_count"],
    "university": ["enrollment", "students", "student_count", "campus_population"],
    "transport_hub": ["daily_ridership", "ridership", "interchange_volume", "line_count"],
    "cruise_port": ["passenger_throughput", "annual_passenger_throughput"],
    "mosque": [
        "mosque_area",
        "gross_floor_area",
        "prayer_hall_area",
        "annual_visitors",
        "annual_visits",
        "daily_visitors",
        "annual_footfall",
        "footfall",
    ],
}

ANNUAL_VISIT_FIELDS = {
    "annual_passenger_throughput",
    "passenger_throughput",
    "annual_footfall",
    "footfall",
    "annual_visitors",
    "annual_visits",
}
DAILY_VISIT_FIELDS = {"daily_ridership", "ridership", "daily_visitors"}
VISIT_VOLUME_FIELDS = {"outpatient_volume", "interchange_volume"}
VISIT_METRIC_FIELDS = ANNUAL_VISIT_FIELDS | DAILY_VISIT_FIELDS | VISIT_VOLUME_FIELDS
VISITOR_PRIMARY_SCENES = {
    "airport_terminal",
    "mall_mixed_use",
    "transport_hub",
    "cruise_port",
    "mosque",
}
AREA_FIELDS = {
    "exhibition_area",
    "meeting_area",
    "meeting_ballroom_area",
    "gla",
    "retail_gfa",
    "office_nla",
    "office_gfa",
    "mosque_area",
    "gross_floor_area",
    "prayer_hall_area",
    "site_area",
    "built_up_area",
}
ROOM_FIELDS = {"keys", "rooms"}
STAFF_COUNT_FIELDS = {"staff_count", "employee_count"}
PEOPLE_CAPACITY_FIELDS = {
    "seat_count",
    "peak_event_capacity",
    "plenary_capacity",
    "ballroom_capacity",
}
FLOOR_COUNT_FIELDS = {"floor_count"}
HEIGHT_FIELDS = {"tower_height"}


class DerivedInfoProvider(Protocol):
    provider_name: str

    def derive(self, packet: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class EvidenceMetric:
    field_key: str
    field_value: str
    source_name: str
    source_tier: str
    source_url: str
    source_date: str | None
    numeric_value: float | None
    numeric_unit: str | None
    priority: tuple[int, int, int]


@dataclass(frozen=True)
class AnnualVisitProxy:
    value: float
    basis: str
    chain: str


@dataclass(frozen=True)
class DerivedValues:
    property_id: str
    scan_run_id: str
    scene_type: str
    annual_visits_est: float | None
    annual_visits_raw: float | None
    area_metric_name: str
    area_metric_value: float | None
    area_metric_unit: str | None
    area_metric_status: str
    primary_value_indicators: list[str]
    secondary_value_indicators: list[str]
    proxy_basis: str
    proxy_level: str
    metric_availability_level: str
    assumption_note: str | None
    evidence_status: str
    value_class: str
    action_class: str
    recommended_solution: str
    reason_to_recommend: str
    risk_review_reason: str | None
    next_action: str
    inference_basis: str
    inference_chain: str
    inference_confidence: str
    review_item: dict[str, Any] | None
    selected_metric: EvidenceMetric | None
    source_domain_count: int
    evidence_count: int


class OpenAIDerivedInfoProvider:
    provider_name = "openai_gpt"

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
                "OPENAI_API_KEY or ISITE2_OPENAI_API_KEY is required for GPT derived refresh"
            )
        self.model = (
            model
            or os.getenv("ISITE2_OPENAI_MODEL")
            or os.getenv("OPENAI_MODEL")
            or "gpt-4.1-mini"
        )
        self.timeout_seconds = timeout_seconds

    def derive(self, packet: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _GPT_SYSTEM_PROMPT},
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
        content = payload["choices"][0]["message"]["content"]
        decision = json.loads(content)
        decision.setdefault("provider_model", self.model)
        decision.setdefault("provider_type", "gpt")
        return decision


class RuleSafetyFallbackProvider:
    """Local fallback used for tests and for environments without a GPT key.

    Production refresh should use CodexOAuthDerivedInfoProvider. This provider keeps the
    pipeline usable and lets quality gates prove that GPT output is not blindly trusted.
    """

    provider_name = "rule_safety_fallback"

    def derive(self, packet: dict[str, Any]) -> dict[str, Any]:
        scene_type = packet["property"]["scene_type"]
        metric = _best_metric(scene_type, packet["evidence_items"])
        source_domains = {
            _domain(item.get("source_url"))
            for item in packet["evidence_items"]
            if item.get("source_url")
        }
        source_domains.discard("")
        scene_rule = get_scene_rule(scene_type)
        annual_proxy = _annual_visits_from_metric(scene_type, metric)
        annual_visits_est = annual_proxy.value if annual_proxy is not None else None
        metric_availability = HARD_EVIDENCE_LABEL if metric else LOW_EVIDENCE_LABEL
        return {
            "metric_availability_level": metric_availability,
            "selected_primary_metric": None
            if metric is None
            else {
                "field_group": metric.field_key,
                "field_value": metric.field_value,
                "source_urls": [metric.source_url],
                "numeric_value": metric.numeric_value,
                "unit": metric.numeric_unit,
            },
            "annual_visits_est": annual_visits_est,
            "annual_visits_basis": "local safety fallback from objective primary metric",
            "capacity_estimate": metric.numeric_value if metric else None,
            "capacity_unit": metric.numeric_unit if metric else None,
            "evidence_status": _evidence_status(metric is not None, len(source_domains)),
            "value_class": _value_class(annual_visits_est),
            "action_class": (
                ActionClass.SURVEY_FIRST.value if metric else ActionClass.REVIEW_QUEUE.value
            ),
            "recommended_solution": RecommendedSolution(scene_rule["default_solution"]).value,
            "reason_to_recommend": short_reason(
                scene_type,
                metric.field_value if metric else "主指标量化证据不足",
                RecommendedSolution(scene_rule["default_solution"]).value,
                inference_used=not (metric and metric.field_key in ANNUAL_VISIT_FIELDS),
            ),
            "next_action": _fallback_next_action(
                packet["property"],
                has_hard_primary=metric is not None,
                source_domain_count=len(source_domains),
            ),
            "inference_basis": metric.field_value if metric else "No hard primary metric",
            "inference_chain": "objective evidence pool -> scene proxy -> derived fields",
            "inference_confidence": "Conservative" if metric else "Moderate",
            "review_items": [],
            "provider_type": "rule",
            "provider_model": self.provider_name,
        }


class CodexOAuthDerivedInfoProvider:
    provider_name = "codex_oauth"

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
            or os.getenv("ISITE2_CODEX_OAUTH_MODEL")
            or os.getenv("ISITE2_OPENAI_MODEL")
            or "gpt-5.4-mini"
        )
        self.reasoning_effort = (
            reasoning_effort
            or os.getenv("ISITE2_CODEX_OAUTH_REASONING_EFFORT")
            or "low"
        )
        self.timeout_seconds = timeout_seconds

    def derive(self, packet: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="isite2_codex_oauth_") as directory:
            temp_dir = Path(directory)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / "decision.json"
            schema_path.write_text(
                json.dumps(_CODEX_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prompt = "\n\n".join(
                [
                    _GPT_SYSTEM_PROMPT,
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
                    f"Codex OAuth GPT call failed with exit {process.returncode}: {stderr}"
                )
            if not output_path.exists():
                raise RuntimeError("Codex OAuth GPT call did not produce output JSON")
            decision = json.loads(output_path.read_text(encoding="utf-8"))
            decision.setdefault("provider_type", "gpt")
            decision.setdefault("provider_model", f"codex-oauth:{self.model}")
            return decision


class FileCachedDerivedInfoProvider:
    provider_name = "file_cached_gpt"

    def __init__(self, provider: DerivedInfoProvider, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self.provider = provider
        self.cache_dir = cache_dir
        self.provider_name = f"cached:{provider.provider_name}"

    def derive(self, packet: dict[str, Any]) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = _packet_hash(packet)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            cached["cache_status"] = "hit"
            return cached
        decision = self.provider.derive(packet)
        decision["cache_status"] = "miss"
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
        return decision


def refresh_active_derived_info(
    engine: Engine,
    *,
    scan_run_id: str | None = None,
    property_ids: list[str] | None = None,
    provider: DerivedInfoProvider | None = None,
    provider_mode: str | None = None,
    limit: int | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    force: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    """Refresh latest active derived fields from objective evidence.

    GPT is used through Codex OAuth by default. OpenAI API key mode is available only
    when provider_mode is explicitly "gpt". Rule fallback is only a local safety
    fallback.
    """
    active_provider = provider or _provider_from_mode(
        provider_mode,
        cache_dir=None if force else cache_dir,
    )
    with engine.begin() as connection:
        _ensure_derived_refresh_status_schema(connection)
        targets = _latest_targets(connection, scan_run_id=scan_run_id)
        if property_ids:
            property_id_set = set(property_ids)
            targets = [
                target for target in targets if target["property_id"] in property_id_set
            ]
        if limit is not None:
            targets = targets[:limit]
        evidence = _evidence_pool(connection, [target["property_id"] for target in targets])
        before = _qa_summary(targets, evidence)

    counts = Counter()
    errors: list[dict[str, str]] = []
    derived_rows: list[DerivedValues] = []

    def persist_decision(
        target: dict[str, Any],
        evidence_rows: list[dict[str, Any]],
        evidence_package_hash: str,
        decision: dict[str, Any],
    ) -> None:
        try:
            derived = _derive_from_decision(target, evidence_rows, decision)
        except Exception as exc:
            errors.append(
                {
                    "property_id": target["property_id"],
                    "property_name": target["canonical_name"],
                    "error": f"derived decision validation failed: {exc}",
                }
            )
            return
        with engine.begin() as connection:
            _write_derived(connection, derived)
            if _should_persist_decision(active_provider):
                _record_derived_refresh_success(
                    connection,
                    property_id=target["property_id"],
                    evidence_package_hash=evidence_package_hash,
                    provider_name=active_provider.provider_name,
                    decision=decision,
                )
        derived_rows.append(derived)
        counts["properties_refreshed"] += 1
        if derived.metric_availability_level == HARD_EVIDENCE_LABEL:
            counts["hard_primary_metric_properties"] += 1
        else:
            counts["low_primary_metric_properties"] += 1
        if derived.source_domain_count >= 2:
            counts["multi_source_properties"] += 1

    concurrency = max(1, int(concurrency))
    pending_provider_calls = []
    for target in targets:
        evidence_rows = evidence.get(target["property_id"], [])
        packet = _gpt_packet(target, evidence_rows)
        evidence_package_hash = _evidence_package_hash(target, evidence_rows)
        cached_decision = None
        if not force:
            with engine.begin() as connection:
                compatible_hashes = [
                    evidence_package_hash,
                    _legacy_evidence_package_hash(target, evidence_rows),
                ]
                source_city = target.get("source_city_before_normalization")
                if source_city and source_city != target.get("city"):
                    compatible_hashes.append(
                        _legacy_evidence_package_hash(
                            target,
                            evidence_rows,
                            city=str(source_city),
                        )
                    )
                for compatible_hash in dict.fromkeys(compatible_hashes):
                    cached_decision = _cached_decision_for_evidence(
                        connection,
                        property_id=target["property_id"],
                        evidence_package_hash=compatible_hash,
                    )
                    if cached_decision is not None:
                        break
        if cached_decision is not None:
            decision = cached_decision
            decision["cache_status"] = "db_evidence_hash_hit"
            counts["skipped_unchanged_evidence"] += 1
            counts["db_decision_reuse_count"] += 1
            persist_decision(target, evidence_rows, evidence_package_hash, decision)
        else:
            counts["provider_call_count"] += 1
            pending_provider_calls.append(
                (target, evidence_rows, evidence_package_hash, packet)
            )

    if concurrency == 1:
        for target, evidence_rows, evidence_package_hash, packet in pending_provider_calls:
            try:
                decision = active_provider.derive(packet)
            except Exception as exc:
                errors.append(
                    {
                        "property_id": target["property_id"],
                        "property_name": target["canonical_name"],
                        "error": str(exc),
                    }
                )
                continue
            if decision.get("cache_status") == "hit":
                counts["file_cache_hit_count"] += 1
            else:
                _increment_provider_request_count(counts, active_provider)
            persist_decision(target, evidence_rows, evidence_package_hash, decision)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(active_provider.derive, packet): (
                    target,
                    evidence_rows,
                    evidence_package_hash,
                )
                for target, evidence_rows, evidence_package_hash, packet in pending_provider_calls
            }
            for future in as_completed(futures):
                target, evidence_rows, evidence_package_hash = futures[future]
                try:
                    decision = future.result()
                except Exception as exc:
                    errors.append(
                        {
                            "property_id": target["property_id"],
                            "property_name": target["canonical_name"],
                            "error": str(exc),
                        }
                    )
                    continue
                if decision.get("cache_status") == "hit":
                    counts["file_cache_hit_count"] += 1
                else:
                    _increment_provider_request_count(counts, active_provider)
                persist_decision(target, evidence_rows, evidence_package_hash, decision)
    with engine.begin() as connection:
        after_targets = _latest_targets(connection, scan_run_id=scan_run_id)
        if property_ids:
            property_id_set = set(property_ids)
            after_targets = [
                target
                for target in after_targets
                if target["property_id"] in property_id_set
            ]
        if limit is not None:
            after_targets = after_targets[:limit]
        after_evidence = _evidence_pool(
            connection,
            [target["property_id"] for target in after_targets],
        )
        after = _qa_summary(after_targets, after_evidence)
    return {
        "mode": "gpt_derived_info_refresh",
        "provider": active_provider.provider_name,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "scan_run_id": scan_run_id,
        "property_ids": property_ids,
        "target_count": len(targets),
        "counts": dict(counts),
        "error_count": len(errors),
        "errors": errors[:25],
        "qa_before": before,
        "qa_after": after,
        "low_evidence_samples": _low_evidence_samples(derived_rows),
    }


def _provider_from_mode(
    mode: str | None,
    *,
    cache_dir: Path | None,
) -> DerivedInfoProvider:
    selected = (
        mode or os.getenv("ISITE2_DERIVED_REFRESH_PROVIDER") or "codex-oauth"
    ).strip().lower()
    if selected == "rule":
        return RuleSafetyFallbackProvider()
    if selected == "codex-oauth":
        provider = CodexOAuthDerivedInfoProvider()
        return FileCachedDerivedInfoProvider(provider, cache_dir) if cache_dir else provider
    if selected == "gpt":
        provider: DerivedInfoProvider = OpenAIDerivedInfoProvider()
        return FileCachedDerivedInfoProvider(provider, cache_dir) if cache_dir else provider
    if selected != "auto":
        raise ValueError("provider_mode must be one of: auto, gpt, codex-oauth, rule")
    if (Path.home() / ".codex" / "auth.json").exists():
        provider = CodexOAuthDerivedInfoProvider()
        return FileCachedDerivedInfoProvider(provider, cache_dir) if cache_dir else provider
    return RuleSafetyFallbackProvider()


def _gpt_packet(target: dict[str, Any], evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    scene_rule = get_scene_rule(target["scene_type"])
    return {
        "task": "derive_iSite2_fields_from_objective_evidence",
        "property": {
            "property_id": target["property_id"],
            "country": target["country"],
            "city": target["city"],
            "property_name": target["canonical_name"],
            "scene_type": target["scene_type"],
            "current_annual_visits_est": target.get("annual_visits_est"),
        },
        "scene_rule": {
            "primary_indicators": scene_rule.get("primary_indicators", []),
            "quantitative_primary_indicators": QUANTITATIVE_PRIMARY.get(target["scene_type"], []),
            "area_metric": scene_rule.get("area_metric"),
            "proxy_basis": scene_rule.get("proxy_basis", []),
            "default_solution": scene_rule.get("default_solution"),
            "demand_parameters": scene_rule.get("demand_parameters", {}),
        },
        "evidence_items": [
            {
                "field_group": row.get("field_group"),
                "indicator_name": row.get("indicator_name"),
                "field_value": row.get("field_value"),
                "unit": row.get("unit"),
                "evidence_type": row.get("evidence_type"),
                "source_name": row.get("source_name"),
                "source_tier": row.get("source_tier"),
                "source_url": row.get("source_url"),
                "source_date": row.get("source_date"),
            }
            for row in evidence_rows[:40]
        ],
        "output_contract": {
            "metric_availability_level": [HARD_EVIDENCE_LABEL, LOW_EVIDENCE_LABEL],
            "evidence_status": [
                EvidenceStatus.SUPPORTED.value,
                EvidenceStatus.INDICATIVE.value,
                EvidenceStatus.INSUFFICIENT.value,
            ],
            "value_class": [value.value for value in ValueClass],
            "action_class": [value.value for value in ActionClass],
            "recommended_solution": [value.value for value in RecommendedSolution],
            "must_return_json": True,
        },
    }


def _latest_targets(connection, *, scan_run_id: str | None) -> list[dict[str, Any]]:
    if scan_run_id:
        rows = connection.execute(
            text(
                """
                SELECT p.id AS property_id, p.country, p.city, p.canonical_name,
                       pca.source_city AS source_city_before_normalization,
                       p.scene_type, sc.scan_run_id, sm.area_metric_name,
                       sm.area_metric_value, sm.area_metric_unit,
                       sm.annual_visits_raw, sm.annual_visits_est,
                       sm.metric_availability_level,
                       EXISTS (
                         SELECT 1
                         FROM review_queue rq
                         WHERE rq.property_id = p.id
                           AND rq.scan_run_id = sc.scan_run_id
                           AND rq.review_type = 'designated_lead_primary_metric'
                           AND rq.status = 'open'
                       ) AS designated_missing_primary_metric
                FROM scan_candidates sc
                JOIN properties p ON p.id = sc.property_id
                LEFT JOIN property_city_assignments pca ON pca.property_id = p.id
                LEFT JOIN scene_model_results sm
                  ON sm.property_id = p.id AND sm.scan_run_id = sc.scan_run_id
                WHERE sc.property_id IS NOT NULL
                  AND sc.scan_run_id = :scan_run_id
                ORDER BY p.country, p.scene_type, p.canonical_name
                """
            ),
            {"scan_run_id": scan_run_id},
        ).mappings().all()
    else:
        rows = connection.execute(
            text(
                """
                WITH latest AS (
                  SELECT sc.*,
                         ROW_NUMBER() OVER (
                           PARTITION BY sc.property_id
                           ORDER BY sc.created_at DESC, sc.id DESC
                         ) AS rn
                  FROM scan_candidates sc
                  WHERE sc.property_id IS NOT NULL
                )
                SELECT p.id AS property_id, p.country, p.city, p.canonical_name,
                       pca.source_city AS source_city_before_normalization,
                       p.scene_type, latest.scan_run_id, sm.area_metric_name,
                       sm.area_metric_value, sm.area_metric_unit,
                       sm.annual_visits_raw, sm.annual_visits_est,
                       sm.metric_availability_level,
                       EXISTS (
                         SELECT 1
                         FROM review_queue rq
                         WHERE rq.property_id = p.id
                           AND rq.scan_run_id = latest.scan_run_id
                           AND rq.review_type = 'designated_lead_primary_metric'
                           AND rq.status = 'open'
                       ) AS designated_missing_primary_metric
                FROM latest
                JOIN properties p ON p.id = latest.property_id
                LEFT JOIN property_city_assignments pca ON pca.property_id = p.id
                LEFT JOIN scene_model_results sm
                  ON sm.property_id = p.id AND sm.scan_run_id = latest.scan_run_id
                WHERE latest.rn = 1
                ORDER BY p.country, p.scene_type, p.canonical_name
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _evidence_pool(connection, property_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not property_ids:
        return {}
    statement = text(
        """
        SELECT id AS evidence_id, property_id, field_group, indicator_name, field_value, unit,
               evidence_type, source_name, source_tier, source_url, source_date
        FROM evidence_items
        WHERE property_id IN :property_ids
        ORDER BY created_at DESC, id DESC
        """
    ).bindparams(bindparam("property_ids", expanding=True))
    rows = connection.execute(statement, {"property_ids": property_ids}).mappings().all()
    by_property: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        item = dict(row)
        key = (
            item["property_id"],
            str(item.get("source_url") or "").strip().casefold(),
            str(item.get("field_group") or "").strip().casefold(),
            str(item.get("indicator_name") or "").strip().casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        by_property[item["property_id"]].append(item)
    return by_property


def _ensure_derived_refresh_status_schema(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS derived_refresh_status (
                property_id TEXT PRIMARY KEY,
                evidence_package_hash TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                decision_json TEXT,
                status TEXT NOT NULL,
                refreshed_at TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
    )


def _cached_decision_for_evidence(
    connection,
    *,
    property_id: str,
    evidence_package_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT decision_json
            FROM derived_refresh_status
            WHERE property_id = :property_id
              AND evidence_package_hash = :evidence_package_hash
              AND status = 'success'
              AND decision_json IS NOT NULL
            """
        ),
        {
            "property_id": property_id,
            "evidence_package_hash": evidence_package_hash,
        },
    ).mappings().first()
    if row is None:
        return None
    try:
        return json.loads(row["decision_json"])
    except json.JSONDecodeError:
        return None


def _record_derived_refresh_success(
    connection,
    *,
    property_id: str,
    evidence_package_hash: str,
    provider_name: str,
    decision: dict[str, Any],
) -> None:
    payload = {
        "property_id": property_id,
        "evidence_package_hash": evidence_package_hash,
        "provider_name": provider_name,
        "decision_json": json.dumps(decision, ensure_ascii=False, sort_keys=True),
        "status": "success",
        "refreshed_at": datetime.now(UTC).isoformat(),
        "error_message": None,
    }
    updated = connection.execute(
        text(
            """
            UPDATE derived_refresh_status
            SET evidence_package_hash = :evidence_package_hash,
                provider_name = :provider_name,
                decision_json = :decision_json,
                status = :status,
                refreshed_at = :refreshed_at,
                error_message = :error_message
            WHERE property_id = :property_id
            """
        ),
        payload,
    ).rowcount
    if updated:
        return
    connection.execute(
        text(
            """
            INSERT INTO derived_refresh_status (
                property_id, evidence_package_hash, provider_name, decision_json,
                status, refreshed_at, error_message
            ) VALUES (
                :property_id, :evidence_package_hash, :provider_name, :decision_json,
                :status, :refreshed_at, :error_message
            )
            """
        ),
        payload,
    )


def _should_persist_decision(provider: DerivedInfoProvider) -> bool:
    return "rule_safety_fallback" not in provider.provider_name


def _increment_provider_request_count(counts: Counter, provider: DerivedInfoProvider) -> None:
    name = provider.provider_name.casefold()
    if any(token in name for token in ("gpt", "codex", "openai")):
        counts["gpt_analysis_requested_count"] += 1
    else:
        counts["rule_analysis_requested_count"] += 1


def _derive_from_decision(
    target: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    decision: dict[str, Any],
) -> DerivedValues:
    scene_type = target["scene_type"]
    scene_rule = get_scene_rule(scene_type)
    objective_metric = _best_metric(scene_type, evidence_rows)
    visit_metric = _best_visit_metric(scene_type, evidence_rows)
    source_domains = {
        _domain(row.get("source_url"))
        for row in evidence_rows
        if row.get("source_url")
    }
    source_domains.discard("")
    source_domain_count = len(source_domains)
    decision_metric = _decision_metric(decision)

    has_hard_primary = objective_metric is not None
    selected_metric = objective_metric if has_hard_primary else None
    if _should_select_visit_metric_as_primary(scene_type, selected_metric, visit_metric):
        selected_metric = visit_metric
    if (
        decision_metric
        and objective_metric
        and decision_metric.field_key == objective_metric.field_key
        and not _should_select_visit_metric_as_primary(
            scene_type,
            objective_metric,
            visit_metric,
        )
    ):
        selected_metric = objective_metric

    current_annual = _float_or_none(target.get("annual_visits_est"))
    gpt_annual = _float_or_none(decision.get("annual_visits_est"))
    annual_metric = visit_metric or selected_metric
    proxy_annual = _annual_visits_from_metric(scene_type, annual_metric)
    fallback_annual = proxy_annual.value if proxy_annual is not None else None
    if not annual_metric:
        annual_est = None
    elif _is_direct_annual_visit_metric(annual_metric):
        annual_est = annual_metric.numeric_value
    elif annual_metric.field_key == "line_count":
        annual_est = None
    elif (
        scene_type == "airport_terminal"
        and annual_metric.field_key == "terminal_capacity"
    ):
        annual_est = (
            gpt_annual
            if gpt_annual is not None and not _is_year_like_annual_visit(gpt_annual)
            else None
        )
    elif fallback_annual is not None:
        annual_est = fallback_annual
    else:
        annual_est = gpt_annual or current_annual
    annual_est = _safe_annual_visit_estimate(annual_est)
    if annual_est is not None and annual_est <= 0:
        annual_est = None
    annual_raw = annual_est if _is_direct_annual_visit_metric(annual_metric) else None

    if not selected_metric:
        has_hard_primary = False
    metric_availability = HARD_EVIDENCE_LABEL if has_hard_primary else LOW_EVIDENCE_LABEL
    capacity_estimate = _float_or_none(decision.get("capacity_estimate"))
    capacity_unit = _clean_optional_text(decision.get("capacity_unit"))
    if selected_metric is not None:
        capacity_estimate = selected_metric.numeric_value
        capacity_unit = selected_metric.numeric_unit

    field_keys = sorted({_field_key(row) for row in evidence_rows if _field_key(row)})
    area_metric_name = _metric_label(
        selected_metric.field_key if selected_metric else scene_rule["area_metric"]
    )
    evidence_status = (
        _valid_choice(
            decision.get("evidence_status"),
            {value.value for value in EvidenceStatus},
            _evidence_status(has_hard_primary, source_domain_count),
        )
        if has_hard_primary
        else EvidenceStatus.INSUFFICIENT.value
    )
    designated_missing_primary_metric = bool(
        target.get("designated_missing_primary_metric")
    )
    action_class = (
        _valid_choice(
            decision.get("action_class"),
            {value.value for value in ActionClass},
            ActionClass.SURVEY_FIRST.value,
        )
        if has_hard_primary
        else (
            ActionClass.SURVEY_FIRST.value
            if designated_missing_primary_metric
            else ActionClass.REVIEW_QUEUE.value
        )
    )
    value_class = _valid_choice(
        decision.get("value_class"),
        {value.value for value in ValueClass},
        _value_class(annual_est),
    )
    if not has_hard_primary:
        value_class = ValueClass.OBSERVATION.value
    recommended_solution = _valid_choice(
        decision.get("recommended_solution"),
        {value.value for value in RecommendedSolution},
        RecommendedSolution(scene_rule["default_solution"]).value,
    )
    reason = _clean_optional_text(decision.get("reason_to_recommend")) or short_reason(
        scene_type,
        selected_metric.field_value if selected_metric else "主指标量化证据不足",
        recommended_solution,
        inference_used=not _is_direct_annual_visit_metric(annual_metric),
    )
    next_action = _clean_optional_text(decision.get("next_action")) or _fallback_next_action(
        target,
        has_hard_primary=has_hard_primary,
        source_domain_count=source_domain_count,
    )
    inference_basis = (
        annual_metric.field_value
        if annual_metric and annual_est is not None
        else selected_metric.field_value
        if selected_metric
        else (
            _clean_optional_text(decision.get("inference_basis"))
            or "No hard primary metric in evidence pool"
        )
    )
    proxy_chain = proxy_annual.chain if proxy_annual is not None else None
    if selected_metric and proxy_chain:
        inference_chain = proxy_chain
    else:
        inference_chain = _clean_optional_text(decision.get("inference_chain")) or (
            proxy_chain
            or (
                "GPT aggregates objective evidence -> applies scene proxy -> "
                "derives visits/capacity/action"
            )
    )
    inference_confidence = _valid_choice(
        decision.get("inference_confidence"),
        {"Conservative", "Moderate"},
        "Conservative" if has_hard_primary else "Moderate",
    )
    review_item = _review_item_from_decision(
        target,
        decision,
        has_hard_primary=has_hard_primary,
        source_domain_count=source_domain_count,
        selected_metric=selected_metric,
    )
    provider_model = _clean_optional_text(decision.get("provider_model"))
    assumption_note = _clean_optional_text(decision.get("assumption_note"))
    if provider_model:
        assumption_note = (
            f"{assumption_note} | GPT model: {provider_model}"
            if assumption_note
            else f"GPT model: {provider_model}"
        )
    if not has_hard_primary:
        assumption_note = (
            f"{assumption_note} | {LOW_EVIDENCE_LABEL}"
            if assumption_note
            else LOW_EVIDENCE_LABEL
        )
        capacity_estimate = None
        capacity_unit = None
    provider_type = _clean_optional_text(decision.get("provider_type")) or "unknown"
    return DerivedValues(
        property_id=target["property_id"],
        scan_run_id=target["scan_run_id"],
        scene_type=scene_type,
        annual_visits_est=annual_est,
        annual_visits_raw=annual_raw,
        area_metric_name=area_metric_name,
        area_metric_value=capacity_estimate,
        area_metric_unit=capacity_unit,
        area_metric_status=(
            "GPT Aggregated Direct Metric"
            if has_hard_primary and provider_type == "gpt"
            else "Rule Safety Aggregated Direct Metric"
            if has_hard_primary
            else "Missing Primary Metric"
        ),
        primary_value_indicators=list(scene_rule.get("primary_indicators", [])),
        secondary_value_indicators=field_keys,
        proxy_basis=_proxy_basis(scene_rule, has_hard_primary),
        proxy_level=ProxyLevel.P1_STRONG.value if has_hard_primary else ProxyLevel.P3_WEAK.value,
        metric_availability_level=metric_availability,
        assumption_note=assumption_note,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        recommended_solution=recommended_solution,
        reason_to_recommend=reason,
        risk_review_reason="室分建设状态无公开证据，现网状态链独立进入核验。",
        next_action=next_action,
        inference_basis=inference_basis,
        inference_chain=inference_chain,
        inference_confidence=inference_confidence,
        review_item=review_item,
        selected_metric=selected_metric,
        source_domain_count=source_domain_count,
        evidence_count=len(evidence_rows),
    )


def _write_derived(connection, derived: DerivedValues) -> None:
    now = datetime.now(UTC).isoformat()
    scene_payload = {
        "property_id": derived.property_id,
        "scan_run_id": derived.scan_run_id,
        "area_metric_name": derived.area_metric_name,
        "area_metric_value": derived.area_metric_value,
        "area_metric_unit": derived.area_metric_unit,
        "area_metric_status": derived.area_metric_status,
        "primary_value_indicators": json.dumps(
            derived.primary_value_indicators,
            ensure_ascii=False,
        ),
        "secondary_value_indicators": json.dumps(
            derived.secondary_value_indicators,
            ensure_ascii=False,
        ),
        "proxy_basis": derived.proxy_basis,
        "proxy_level": derived.proxy_level,
        "annual_visits_raw": derived.annual_visits_raw,
        "annual_visits_est": derived.annual_visits_est,
        "metric_availability_level": derived.metric_availability_level,
        "assumption_note": derived.assumption_note,
    }
    updated = connection.execute(
        text(
            """
            UPDATE scene_model_results
            SET area_metric_name = :area_metric_name,
                area_metric_value = :area_metric_value,
                area_metric_unit = :area_metric_unit,
                area_metric_status = :area_metric_status,
                primary_value_indicators = :primary_value_indicators,
                secondary_value_indicators = :secondary_value_indicators,
                proxy_basis = :proxy_basis,
                proxy_level = :proxy_level,
                annual_visits_raw = :annual_visits_raw,
                annual_visits_est = :annual_visits_est,
                metric_availability_level = :metric_availability_level,
                assumption_note = :assumption_note
            WHERE property_id = :property_id
              AND scan_run_id = :scan_run_id
            """
        ),
        scene_payload,
    ).rowcount
    if not updated:
        connection.execute(
            text(
                """
                INSERT INTO scene_model_results (
                    id, property_id, scan_run_id, area_metric_name, area_metric_value,
                    area_metric_unit, area_metric_status, primary_value_indicators,
                    secondary_value_indicators, proxy_basis, proxy_level,
                    annual_visits_raw, annual_visits_est, metric_availability_level,
                    assumption_note, created_at
                ) VALUES (
                    :id, :property_id, :scan_run_id, :area_metric_name, :area_metric_value,
                    :area_metric_unit, :area_metric_status, :primary_value_indicators,
                    :secondary_value_indicators, :proxy_basis, :proxy_level,
                    :annual_visits_raw, :annual_visits_est, :metric_availability_level,
                    :assumption_note, :created_at
                )
                """
            ),
            {**scene_payload, "id": str(uuid4()), "created_at": now},
        )

    demand = calculate_demand(
        derived.annual_visits_est,
        demand_params_from_scene_rule(get_scene_rule(derived.scene_type)),
    )
    demand_payload = {
        "property_id": derived.property_id,
        "scan_run_id": derived.scan_run_id,
        "daily_visits": demand.daily_visits,
        "attach_rate": demand.attach_rate,
        "indoor_capture": demand.indoor_capture,
        "busy_hour_factor": demand.busy_hour_factor,
        "gb_per_user_busy_hour": demand.gb_per_user_busy_hour,
        "busy_hour_users": demand.busy_hour_users,
        "busy_hour_traffic_gb": demand.busy_hour_traffic_gb,
        "busy_hour_bandwidth_mbps": demand.busy_hour_bandwidth_mbps,
        "cannot_calculate_reason": demand.cannot_calculate_reason,
    }
    if not connection.execute(
        text(
            """
            UPDATE demand_estimates
            SET daily_visits = :daily_visits,
                attach_rate = :attach_rate,
                indoor_capture = :indoor_capture,
                busy_hour_factor = :busy_hour_factor,
                gb_per_user_busy_hour = :gb_per_user_busy_hour,
                busy_hour_users = :busy_hour_users,
                busy_hour_traffic_gb = :busy_hour_traffic_gb,
                busy_hour_bandwidth_mbps = :busy_hour_bandwidth_mbps,
                cannot_calculate_reason = :cannot_calculate_reason
            WHERE property_id = :property_id
              AND scan_run_id = :scan_run_id
            """
        ),
        demand_payload,
    ).rowcount:
        connection.execute(
            text(
                """
                INSERT INTO demand_estimates (
                    id, property_id, scan_run_id, daily_visits, attach_rate,
                    indoor_capture, busy_hour_factor, gb_per_user_busy_hour,
                    busy_hour_users, busy_hour_traffic_gb, busy_hour_bandwidth_mbps,
                    cannot_calculate_reason, created_at
                ) VALUES (
                    :id, :property_id, :scan_run_id, :daily_visits, :attach_rate,
                    :indoor_capture, :busy_hour_factor, :gb_per_user_busy_hour,
                    :busy_hour_users, :busy_hour_traffic_gb, :busy_hour_bandwidth_mbps,
                    :cannot_calculate_reason, :created_at
                )
                """
            ),
            {**demand_payload, "id": str(uuid4()), "created_at": now},
        )

    connection.execute(
        text(
            """
            DELETE FROM inference_records
            WHERE property_id = :property_id
              AND scan_run_id = :scan_run_id
            """
        ),
        {"property_id": derived.property_id, "scan_run_id": derived.scan_run_id},
    )
    if derived.annual_visits_est is not None:
        _insert_inference_record(
            connection,
            property_id=derived.property_id,
            scan_run_id=derived.scan_run_id,
            inferred_field="annual_visits_est",
            inferred_value=str(int(derived.annual_visits_est)),
            inference_basis=derived.inference_basis,
            inference_chain=derived.inference_chain,
            inference_confidence=derived.inference_confidence,
            created_at=now,
        )
    if demand.busy_hour_users is not None:
        demand_chain = _demand_inference_chain(derived, demand)
        _insert_inference_record(
            connection,
            property_id=derived.property_id,
            scan_run_id=derived.scan_run_id,
            inferred_field="busy_hour_users",
            inferred_value=f"{demand.busy_hour_users:.2f}",
            inference_basis="annual_visits_est and scene demand parameters",
            inference_chain=demand_chain,
            inference_confidence=derived.inference_confidence,
            created_at=now,
        )
    if demand.busy_hour_traffic_gb is not None:
        _insert_inference_record(
            connection,
            property_id=derived.property_id,
            scan_run_id=derived.scan_run_id,
            inferred_field="busy_hour_traffic_gb",
            inferred_value=f"{demand.busy_hour_traffic_gb:.2f}",
            inference_basis="annual_visits_est and scene demand parameters",
            inference_chain=demand_chain,
            inference_confidence=derived.inference_confidence,
            created_at=now,
        )
    if demand.busy_hour_bandwidth_mbps is not None:
        _insert_inference_record(
            connection,
            property_id=derived.property_id,
            scan_run_id=derived.scan_run_id,
            inferred_field="busy_hour_bandwidth_mbps",
            inferred_value=f"{demand.busy_hour_bandwidth_mbps:.2f}",
            inference_basis="busy_hour_traffic_gb converted to Mbps",
            inference_chain=(
                f"{demand_chain}; bandwidth_mbps = busy_hour_traffic_gb x 1024 x 8 / 3600"
            ),
            inference_confidence=derived.inference_confidence,
            created_at=now,
        )

    conclusion_payload = {
        "property_id": derived.property_id,
        "scan_run_id": derived.scan_run_id,
        "evidence_status": derived.evidence_status,
        "value_class": derived.value_class,
        "action_class": derived.action_class,
        "recommended_solution": derived.recommended_solution,
        "reason_to_recommend": derived.reason_to_recommend,
        "risk_review_reason": derived.risk_review_reason,
        "next_action": derived.next_action,
    }
    if not connection.execute(
        text(
            """
            UPDATE conclusions
            SET evidence_status = :evidence_status,
                value_class = :value_class,
                action_class = :action_class,
                recommended_solution = :recommended_solution,
                reason_to_recommend = :reason_to_recommend,
                risk_review_reason = :risk_review_reason,
                next_action = :next_action
            WHERE property_id = :property_id
              AND scan_run_id = :scan_run_id
            """
        ),
        conclusion_payload,
    ).rowcount:
        connection.execute(
            text(
                """
                INSERT INTO conclusions (
                    id, property_id, scan_run_id, evidence_status, value_class,
                    action_class, recommended_solution, reason_to_recommend,
                    risk_review_reason, next_action, created_at
                ) VALUES (
                    :id, :property_id, :scan_run_id, :evidence_status, :value_class,
                    :action_class, :recommended_solution, :reason_to_recommend,
                    :risk_review_reason, :next_action, :created_at
                )
                """
            ),
            {**conclusion_payload, "id": str(uuid4()), "created_at": now},
        )

    connection.execute(
        text(
            """
            DELETE FROM review_queue
            WHERE property_id = :property_id
              AND scan_run_id = :scan_run_id
              AND review_type = :review_type
            """
        ),
        {
            "property_id": derived.property_id,
            "scan_run_id": derived.scan_run_id,
            "review_type": REVIEW_TYPE,
        },
    )
    if derived.review_item:
        connection.execute(
            text(
                """
                INSERT INTO review_queue (
                    id, property_id, scan_run_id, reason, next_action, status,
                    review_type, severity, gate_name, field_path, blocking_surfaces,
                    source_url, suggested_query, owner, created_at, closed_at
                ) VALUES (
                    :id, :property_id, :scan_run_id, :reason, :next_action, 'open',
                    :review_type, :severity, :gate_name, :field_path, :blocking_surfaces,
                    :source_url, :suggested_query, NULL, :created_at, NULL
                )
                """
            ),
            {
                **derived.review_item,
                "id": str(uuid4()),
                "property_id": derived.property_id,
                "scan_run_id": derived.scan_run_id,
                "created_at": now,
            },
        )


def _insert_inference_record(
    connection,
    *,
    property_id: str,
    scan_run_id: str,
    inferred_field: str,
    inferred_value: str,
    inference_basis: str,
    inference_chain: str,
    inference_confidence: str,
    created_at: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO inference_records (
                id, property_id, scan_run_id, inferred_field, inferred_value,
                inference_basis, inference_chain, inference_confidence, created_at
            ) VALUES (
                :id, :property_id, :scan_run_id, :inferred_field, :inferred_value,
                :inference_basis, :inference_chain, :inference_confidence, :created_at
            )
            """
        ),
        {
            "id": str(uuid4()),
            "property_id": property_id,
            "scan_run_id": scan_run_id,
            "inferred_field": inferred_field,
            "inferred_value": inferred_value,
            "inference_basis": inference_basis,
            "inference_chain": inference_chain,
            "inference_confidence": inference_confidence,
            "created_at": created_at,
        },
    )


def _demand_inference_chain(derived: DerivedValues, demand: Any) -> str:
    return (
        f"annual_visits_est={derived.annual_visits_est:g} / 365 -> daily_visits="
        f"{demand.daily_visits:.2f}; busy_hour_users = daily_visits x attach_rate "
        f"{demand.attach_rate:g} x indoor_capture {demand.indoor_capture:g} x "
        f"busy_hour_factor {demand.busy_hour_factor:g}; busy_hour_traffic_gb = "
        f"busy_hour_users x gb_per_user_busy_hour {demand.gb_per_user_busy_hour:g}"
    )


def _best_metric(scene_type: str, evidence_rows: list[dict[str, Any]]) -> EvidenceMetric | None:
    candidates = _metric_candidates(scene_type, evidence_rows)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.priority)[0]


def _best_visit_metric(
    scene_type: str,
    evidence_rows: list[dict[str, Any]],
) -> EvidenceMetric | None:
    candidates = [
        metric
        for metric in _metric_candidates(scene_type, evidence_rows)
        if metric.field_key in VISIT_METRIC_FIELDS
        and metric.numeric_unit in {"visits/year", "visits/day"}
    ]
    candidates.extend(_embedded_visit_metric_candidates(scene_type, evidence_rows))
    if not candidates:
        return None

    def priority(metric: EvidenceMetric) -> tuple[int, int, int, float]:
        tier_rank = {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2}.get(metric.source_tier, 3)
        unit_rank = 0 if metric.numeric_unit == "visits/year" else 1
        field_rank = 0 if metric.field_key in ANNUAL_VISIT_FIELDS else 1
        return (unit_rank, field_rank, tier_rank, -float(metric.numeric_value or 0))

    return sorted(candidates, key=priority)[0]


def _embedded_visit_metric_candidates(
    scene_type: str,
    evidence_rows: list[dict[str, Any]],
) -> list[EvidenceMetric]:
    if scene_type not in VISITOR_PRIMARY_SCENES and scene_type not in {"hospital"}:
        return []
    candidates: list[EvidenceMetric] = []
    accepted = QUANTITATIVE_PRIMARY.get(scene_type, [])
    accepted_set = set(accepted)
    for row in evidence_rows:
        field_key = _field_key(row)
        if field_key not in accepted_set:
            continue
        if field_key in VISIT_METRIC_FIELDS:
            continue
        text_value = str(row.get("field_value") or "")
        lower = text_value.casefold()
        if not _has_digit(lower) or not _has_visit_context(lower):
            continue
        annual_numbers = _period_matched_visit_numbers(lower, ANNUAL_PERIOD_MARKERS)
        daily_value = _daily_visit_value(lower)
        if annual_numbers:
            numeric_value = max(annual_numbers)
            numeric_unit = "visits/year"
            derived_key = "annual_footfall"
        elif daily_value is not None:
            numeric_value = daily_value
            numeric_unit = "visits/day"
            derived_key = "daily_visitors"
        else:
            continue
        tier_rank = {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2}.get(
            str(row.get("source_tier") or ""),
            3,
        )
        candidates.append(
            EvidenceMetric(
                field_key=derived_key,
                field_value=text_value.strip(),
                source_name=str(row.get("source_name") or ""),
                source_tier=str(row.get("source_tier") or ""),
                source_url=str(row.get("source_url") or ""),
                source_date=row.get("source_date"),
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                priority=(-1, tier_rank, -int(numeric_value)),
            )
        )
    return candidates


def _should_select_visit_metric_as_primary(
    scene_type: str,
    selected_metric: EvidenceMetric | None,
    visit_metric: EvidenceMetric | None,
) -> bool:
    if visit_metric is None:
        return False
    if selected_metric is None:
        return scene_type in VISITOR_PRIMARY_SCENES
    if selected_metric.field_key in VISIT_METRIC_FIELDS:
        return False
    return scene_type in VISITOR_PRIMARY_SCENES


def _metric_candidates(
    scene_type: str,
    evidence_rows: list[dict[str, Any]],
) -> list[EvidenceMetric]:
    accepted = QUANTITATIVE_PRIMARY.get(scene_type, [])
    accepted_set = set(accepted)
    candidates: list[EvidenceMetric] = []
    for row in evidence_rows:
        field_key = _field_key(row)
        if field_key not in accepted_set:
            continue
        value = str(row.get("field_value") or "")
        if not _has_digit(value):
            continue
        numeric_value, numeric_unit = _numeric_value_and_unit(field_key, value)
        if numeric_value is None:
            continue
        tier_rank = {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2}.get(
            str(row.get("source_tier") or ""),
            3,
        )
        indicator_rank = accepted.index(field_key)
        candidates.append(
            EvidenceMetric(
                field_key=field_key,
                field_value=value.strip(),
                source_name=str(row.get("source_name") or ""),
                source_tier=str(row.get("source_tier") or ""),
                source_url=str(row.get("source_url") or ""),
                source_date=row.get("source_date"),
                numeric_value=numeric_value,
                numeric_unit=numeric_unit,
                priority=(indicator_rank, tier_rank, -int(numeric_value)),
            )
        )
    return candidates


def _decision_metric(decision: dict[str, Any]) -> EvidenceMetric | None:
    payload = decision.get("selected_primary_metric")
    if not isinstance(payload, dict):
        return None
    field_key = _normalize_key(
        str(payload.get("field_group") or payload.get("indicator_name") or "")
    )
    field_value = str(payload.get("field_value") or "")
    numeric_value = _float_or_none(payload.get("numeric_value"))
    unit = _clean_optional_text(payload.get("unit"))
    return EvidenceMetric(
        field_key=field_key,
        field_value=field_value,
        source_name="GPT selected metric",
        source_tier="",
        source_url="",
        source_date=None,
        numeric_value=numeric_value,
        numeric_unit=unit,
        priority=(0, 0, 0),
    )


def _qa_summary(targets: list[dict[str, Any]], evidence: dict[str, list[dict[str, Any]]]) -> dict:
    scenes = scene_definitions()
    primary_by_scene = {
        scene: set(config.get("primary_indicators", []))
        for scene, config in scenes.items()
    }
    totals = Counter(target["scene_type"] for target in targets)
    hard = Counter()
    primary_any = Counter()
    multi_source = Counter()
    evidence_counts = Counter()
    source_domains: dict[str, Counter[str]] = defaultdict(Counter)
    for target in targets:
        rows = evidence.get(target["property_id"], [])
        scene = target["scene_type"]
        evidence_counts[scene] += len(rows)
        domains = {_domain(row.get("source_url")) for row in rows if row.get("source_url")}
        domains.discard("")
        if len(domains) >= 2:
            multi_source[scene] += 1
        for domain in domains:
            source_domains[scene][domain] += 1
        keys = {_field_key(row) for row in rows}
        if keys & primary_by_scene.get(scene, set()):
            primary_any[scene] += 1
        if _best_metric(scene, rows):
            hard[scene] += 1
    return {
        "total_properties": len(targets),
        "scene_summary": {
            scene: {
                "total": totals[scene],
                "primary_indicator_properties": primary_any[scene],
                "hard_primary_metric_properties": hard[scene],
                "low_primary_metric_properties": totals[scene] - hard[scene],
                "multi_source_properties": multi_source[scene],
                "evidence_items": evidence_counts[scene],
                "top_source_domains": source_domains[scene].most_common(5),
            }
            for scene in sorted(totals)
        },
        "data_integrity_gate": _data_integrity_gate(targets, evidence),
    }


def _data_integrity_gate(
    targets: list[dict[str, Any]],
    evidence: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    year_like_samples: list[dict[str, Any]] = []
    area_metric_year_like_samples: list[dict[str, Any]] = []
    airport_role_samples: list[dict[str, Any]] = []
    transport_line_annual_samples: list[dict[str, Any]] = []
    line_count_not_selected_samples: list[dict[str, Any]] = []
    wikidata_property_id_samples: list[dict[str, Any]] = []
    daily_as_annual_samples: list[dict[str, Any]] = []
    direct_visit_overridden_samples: list[dict[str, Any]] = []
    hard_metric_not_materialized_samples: list[dict[str, Any]] = []
    implausible_annual_samples: list[dict[str, Any]] = []
    implausible_area_metric_samples: list[dict[str, Any]] = []
    low_evidence_high_value_samples: list[dict[str, Any]] = []

    counts = Counter()
    for target in targets:
        scene_type = target["scene_type"]
        annual_visits_est = _float_or_none(target.get("annual_visits_est"))
        area_metric_name = _normalize_key(str(target.get("area_metric_name") or ""))
        area_metric_value = _float_or_none(target.get("area_metric_value"))
        metric_availability = str(target.get("metric_availability_level") or "")
        rows = evidence.get(target["property_id"], [])
        metric = _best_metric(scene_type, rows)
        visit_metric = _best_visit_metric(scene_type, rows)

        if metric is not None and (
            metric_availability != HARD_EVIDENCE_LABEL or area_metric_value is None
        ):
            counts["hard_primary_metric_not_materialized_count"] += 1
            _append_gate_sample(
                hard_metric_not_materialized_samples,
                target,
                "hard primary metric exists in evidence but active derived fields are not materialized",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value,
            )

        if _is_year_like_annual_visit(annual_visits_est):
            counts["year_like_annual_visits_count"] += 1
            _append_gate_sample(
                year_like_samples,
                target,
                "annual_visits_est looks like a reporting year",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        annual_reason = _implausible_annual_reason(scene_type, annual_visits_est)
        if annual_reason:
            counts["implausible_annual_visits_count"] += 1
            _append_gate_sample(
                implausible_annual_samples,
                target,
                annual_reason,
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        area_reason = _implausible_area_metric_reason(
            scene_type,
            str(target.get("area_metric_name") or ""),
            str(target.get("area_metric_unit") or ""),
            area_metric_value,
        )
        if area_reason:
            counts["implausible_area_metric_value_count"] += 1
            _append_gate_sample(
                implausible_area_metric_samples,
                target,
                area_reason,
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        if metric_availability == LOW_EVIDENCE_LABEL and str(
            target.get("value_class") or ""
        ) in {ValueClass.NATIONAL_FLAGSHIP.value, ValueClass.CITY_CORE.value}:
            counts["low_evidence_high_value_count"] += 1
            _append_gate_sample(
                low_evidence_high_value_samples,
                target,
                "low primary metric evidence must not carry high value_class",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        if (
            _is_year_like_annual_visit(area_metric_value)
            and _metric_value_looks_like_reporting_year(rows, area_metric_value)
        ):
            counts["year_like_area_metric_value_count"] += 1
            _append_gate_sample(
                area_metric_year_like_samples,
                target,
                "area_metric_value appears to have been parsed from a reporting year",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        if (
            metric is not None
            and metric.numeric_unit == "visits/day"
            and annual_visits_est is not None
            and metric.numeric_value is not None
            and annual_visits_est <= metric.numeric_value * 31
        ):
            counts["daily_metric_materialized_as_annual_count"] += 1
            _append_gate_sample(
                daily_as_annual_samples,
                target,
                "daily/business-day metric appears to have been stored as annual_visits_est",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value,
            )

        visit_proxy = _annual_visits_from_metric(scene_type, visit_metric)
        if (
            visit_metric is not None
            and visit_proxy is not None
            and metric is not None
            and metric.field_key not in VISIT_METRIC_FIELDS
            and annual_visits_est is not None
            and abs(annual_visits_est - visit_proxy.value) > max(
                1.0,
                visit_proxy.value * 0.02,
            )
        ):
            counts["direct_visit_metric_overridden_count"] += 1
            _append_gate_sample(
                direct_visit_overridden_samples,
                target,
                "direct daily/year visit evidence was overridden by a capacity/area proxy",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=visit_metric.field_value,
            )

        if scene_type == "airport_terminal" and area_metric_name in {
            "gateway_role",
            "hub_role",
            "strategic_role",
            "terminal_role",
            "airport_role",
        }:
            counts["airport_role_primary_metric_count"] += 1
            _append_gate_sample(
                airport_role_samples,
                target,
                "airport role evidence was selected as the primary metric",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        if (
            scene_type == "transport_hub"
            and area_metric_name == "line_count"
            and annual_visits_est is not None
        ):
            counts["transport_line_count_with_annual_visits_count"] += 1
            _append_gate_sample(
                transport_line_annual_samples,
                target,
                "transport line_count produced annual_visits_est",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

        if (
            scene_type == "transport_hub"
            and metric is not None
            and metric.field_key == "line_count"
            and area_metric_name != "line_count"
        ):
            counts["transport_line_count_evidence_not_selected_count"] += 1
            _append_gate_sample(
                line_count_not_selected_samples,
                target,
                "valid line_count evidence was not selected as the scene primary metric",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value,
            )

        if _has_wikidata_property_id_pollution(
            rows,
            area_metric_value=area_metric_value,
            annual_visits_est=annual_visits_est,
        ):
            counts["wikidata_property_id_metric_pollution_count"] += 1
            _append_gate_sample(
                wikidata_property_id_samples,
                target,
                "Wikidata property id appears to have been parsed as a metric value",
                annual_visits_est=annual_visits_est,
                area_metric_name=target.get("area_metric_name"),
                area_metric_value=area_metric_value,
                evidence_value=metric.field_value if metric else None,
            )

    blocking_issue_count = sum(counts.values())
    return {
        "passed": blocking_issue_count == 0,
        "blocking_issue_count": blocking_issue_count,
        "counts": dict(counts),
        "samples": {
            "year_like_annual_visits": year_like_samples,
            "year_like_area_metric_value": area_metric_year_like_samples,
            "airport_role_primary_metric": airport_role_samples,
            "transport_line_count_with_annual_visits": transport_line_annual_samples,
            "transport_line_count_evidence_not_selected": line_count_not_selected_samples,
            "wikidata_property_id_metric_pollution": wikidata_property_id_samples,
            "daily_metric_materialized_as_annual": daily_as_annual_samples,
            "direct_visit_metric_overridden": direct_visit_overridden_samples,
            "hard_primary_metric_not_materialized": hard_metric_not_materialized_samples,
            "implausible_annual_visits": implausible_annual_samples,
            "implausible_area_metric_value": implausible_area_metric_samples,
            "low_evidence_high_value": low_evidence_high_value_samples,
        },
    }


def _append_gate_sample(
    samples: list[dict[str, Any]],
    target: dict[str, Any],
    issue: str,
    *,
    annual_visits_est: float | None,
    area_metric_name: Any,
    area_metric_value: float | None,
    evidence_value: str | None,
) -> None:
    if len(samples) >= 10:
        return
    samples.append(
        {
            "property_id": target["property_id"],
            "scan_run_id": target["scan_run_id"],
            "country": target["country"],
            "city": target["city"],
            "property_name": target["canonical_name"],
            "scene_type": target["scene_type"],
            "issue": issue,
            "annual_visits_est": annual_visits_est,
            "area_metric_name": area_metric_name,
            "area_metric_value": area_metric_value,
            "evidence_value": evidence_value,
        }
    )


def _is_year_like_annual_visit(value: float | None) -> bool:
    return is_year_like_annual_visit(value)


def _safe_annual_visit_estimate(value: float | None) -> float | None:
    return safe_annual_visit_estimate(value)


def _metric_value_looks_like_reporting_year(
    evidence_rows: list[dict[str, Any]],
    value: float | None,
) -> bool:
    numeric_value = _float_or_none(value)
    if numeric_value is None or not numeric_value.is_integer():
        return False
    year = str(int(numeric_value))
    year_token = re.compile(rf"(?<![\d,.]){re.escape(year)}[.,]?(?![\d,.])")
    year_context = re.compile(
        rf"(?:as\s+of|in|by|since|commissioned(?:\s+on)?|opened(?:\s+on)?|opening|from)\s+"
        rf"(?:[A-Za-z]+\s+\d{{1,2}},?\s+)?{re.escape(year)}[.,]?"
        rf"|{re.escape(year)}[.,]?\s+(?:report|statistics|traffic|fiscal|calendar|data|source)",
        re.IGNORECASE,
    )
    for row in evidence_rows:
        text_value = str(row.get("field_value") or "")
        if year_token.search(text_value) and year_context.search(text_value):
            return True
    return False


def _has_wikidata_property_id_pollution(
    evidence_rows: list[dict[str, Any]],
    *,
    area_metric_value: float | None,
    annual_visits_est: float | None,
) -> bool:
    property_ids = {
        "p81": 81,
        "p3872": 3872,
    }
    values = {
        int(value)
        for value in [area_metric_value, annual_visits_est]
        if value is not None and float(value).is_integer()
    }
    if not values:
        return False
    for row in evidence_rows:
        text_value = str(row.get("field_value") or "").casefold()
        for property_id, numeric_value in property_ids.items():
            if property_id in text_value and numeric_value in values:
                return True
    return False


def _implausible_annual_reason(
    scene_type: str,
    annual_visits_est: float | None,
) -> str | None:
    value = _float_or_none(annual_visits_est)
    if value is None:
        return None
    caps = {
        "airport_terminal": 300_000_000,
        "convention_center": 100_000_000,
        "stadium": 50_000_000,
        "luxury_hotel_mice": 250_000_000,
        "mall_mixed_use": 2_000_000_000,
        "office_government": 1_000_000_000,
        "hospital": 500_000_000,
        "university": 500_000_000,
        "transport_hub": 5_000_000_000,
        "cruise_port": 100_000_000,
        "mosque": 500_000_000,
    }
    cap = caps.get(scene_type, 1_000_000_000)
    if value > cap:
        return f"annual_visits_est exceeds plausible {scene_type} cap {cap:g}"
    return None


def _implausible_area_metric_reason(
    scene_type: str,
    area_metric_name: str,
    area_metric_unit: str,
    area_metric_value: float | None,
) -> str | None:
    value = _float_or_none(area_metric_value)
    if value is None:
        return None
    name = _normalize_key(area_metric_name)
    unit = _normalize_key(area_metric_unit)
    if scene_type == "stadium" and name == "seat_count" and value > 250_000:
        return "stadium seat_count exceeds plausible venue capacity"
    if scene_type == "airport_terminal" and name == "annual_passenger_throughput" and value > 300_000_000:
        return "airport passenger throughput exceeds plausible airport cap"
    if scene_type == "transport_hub" and name == "daily_ridership" and value > 20_000_000:
        return "transport daily ridership exceeds plausible daily cap"
    if scene_type == "transport_hub" and name == "line_count" and value > 20:
        return "transport line_count exceeds plausible interchange line count"
    if scene_type == "luxury_hotel_mice" and name == "keys" and value > 5_000:
        return "hotel keys exceeds plausible property room count"
    if scene_type == "hospital" and name == "beds" and value > 10_000:
        return "hospital beds exceeds plausible single-facility bed count"
    if scene_type == "convention_center" and name in {"exhibition_area", "meeting_area"} and value > 1_000_000:
        return "convention area exceeds plausible venue area"
    if scene_type == "mall_mixed_use" and name == "gla" and value > 1_500_000:
        return "mall GLA exceeds plausible property area"
    if scene_type == "office_government" and name in {"nla", "office_gfa", "office_nla"}:
        if unit and unit not in {"sqm", "m2", "sq_m", "square_meters", "square_metres"}:
            return "office area metric uses non-area unit"
        if value > 5_000_000:
            return "office area exceeds plausible building area"
    if name in {"gla", "nla", "exhibition_area", "meeting_area", "mosque_area"} and unit:
        if unit in {"people", "persons", "persons_day", "visits_year", "daily_visits"}:
            return "area metric uses people/visit unit"
    return None


def _low_evidence_samples(derived_rows: list[DerivedValues]) -> list[dict[str, Any]]:
    samples = []
    for row in derived_rows:
        if row.metric_availability_level == HARD_EVIDENCE_LABEL:
            continue
        samples.append(
            {
                "property_id": row.property_id,
                "scan_run_id": row.scan_run_id,
                "scene_type": row.scene_type,
                "evidence_count": row.evidence_count,
                "next_action": row.next_action,
            }
        )
        if len(samples) >= 25:
            break
    return samples


def _review_item_from_decision(
    target: dict[str, Any],
    decision: dict[str, Any],
    *,
    has_hard_primary: bool,
    source_domain_count: int,
    selected_metric: EvidenceMetric | None,
) -> dict[str, Any] | None:
    review_payloads = decision.get("review_items")
    review_payload = (
        review_payloads[0]
        if isinstance(review_payloads, list) and review_payloads
        else {}
    )
    if has_hard_primary and source_domain_count >= 2 and not review_payload:
        return None
    reason = _clean_optional_text(
        review_payload.get("reason") if isinstance(review_payload, dict) else None
    )
    next_action = _clean_optional_text(
        review_payload.get("next_action") if isinstance(review_payload, dict) else None
    )
    if not reason:
        reason = (
            "低证据标签：未找到场景硬主指标证据；描述性角色证据不计入主指标。"
            if not has_hard_primary
            else "主指标已有量化证据，但仍缺第二独立来源交叉核验。"
        )
    if not next_action:
        next_action = _fallback_next_action(
            target,
            has_hard_primary=has_hard_primary,
            source_domain_count=source_domain_count,
        )
    return {
        "reason": reason,
        "next_action": next_action,
        "review_type": REVIEW_TYPE,
        "severity": "medium",
        "gate_name": "gpt_primary_metric_aggregation_gate",
        "field_path": "evidence_items[].field_group",
        "blocking_surfaces": json.dumps(["recommendation", "export"], ensure_ascii=False),
        "source_url": selected_metric.source_url if selected_metric else None,
        "suggested_query": _suggested_query(target),
    }


def _fallback_next_action(
    target: dict[str, Any],
    *,
    has_hard_primary: bool,
    source_domain_count: int,
) -> str:
    name = target["canonical_name"] if "canonical_name" in target else target["property_name"]
    scene = target["scene_type"]
    if not has_hard_primary:
        return _hard_metric_action(name, scene)
    if source_domain_count < 2:
        return f"补充 {name} 的第二独立来源，交叉核验当前主指标数值与年份。"
    return f"补查 {name} 的运营商室分公告、业主网络升级公告或现场勘测记录。"


def _hard_metric_action(name: str, scene: str) -> str:
    if scene == "airport_terminal":
        return f"补查 {name} 的机场运营方或民航主管机构年度客流/航站楼容量统计。"
    if scene == "stadium":
        return f"核验 {name} 的官方座位容量、年度赛事或峰值入场容量。"
    if scene == "convention_center":
        return f"补查 {name} 的官方 factsheet，提取展览面积、会议面积或峰值容量。"
    if scene == "mall_mixed_use":
        return f"补查 {name} 的运营方/开发商披露 GLA 或年度客流。"
    if scene == "luxury_hotel_mice":
        return f"补查 {name} 的官方客房数、会议面积或宴会厅容量。"
    if scene == "transport_hub":
        return f"补查 {name} 的官方日客流、换乘量或线路数量。"
    return f"补查 {name} 的场景主指标量化证据，并记录来源链接和日期。"


def _suggested_query(target: dict[str, Any]) -> str:
    indicators = " OR ".join(QUANTITATIVE_PRIMARY.get(target["scene_type"], []))
    name = target["canonical_name"] if "canonical_name" in target else target["property_name"]
    return f"{name} {target['country']} {indicators} official"


def _evidence_status(has_hard_primary: bool, source_domain_count: int) -> str:
    if not has_hard_primary:
        return EvidenceStatus.INSUFFICIENT.value
    if source_domain_count >= 2:
        return EvidenceStatus.SUPPORTED.value
    return EvidenceStatus.INDICATIVE.value


def _value_class(annual_visits_est: float | None) -> str:
    value = annual_visits_est or 0
    if value >= 10_000_000:
        return ValueClass.NATIONAL_FLAGSHIP.value
    if value >= 1_000_000:
        return ValueClass.CITY_CORE.value
    if value >= 250_000:
        return ValueClass.REGIONAL_ANCHOR.value
    if value > 0:
        return ValueClass.LOCAL_CANDIDATE.value
    return ValueClass.OBSERVATION.value


def _proxy_basis(scene_rule: dict[str, Any], has_hard_primary: bool) -> str:
    options = scene_rule.get("proxy_basis", [])
    if has_hard_primary and options:
        return options[0]
    if len(options) >= 2:
        return options[1]
    return "Evidence pool metric proxy"


def _annual_visits_from_metric(
    scene_type: str,
    metric: EvidenceMetric | None,
) -> AnnualVisitProxy | None:
    if metric is None or metric.numeric_value is None:
        return None
    value = metric.numeric_value
    field_key = metric.field_key
    if metric.numeric_unit == "visits/day":
        return _annual_proxy(value, 365, metric, "daily/business-day metric x 365 days")
    if _is_direct_annual_visit_metric(metric):
        return AnnualVisitProxy(
            value=value,
            basis=f"direct annual visit metric: {field_key}",
            chain=(
                f"{field_key} direct evidence ({metric.field_value}) -> "
                f"annual_visits_est={int(value)}"
            ),
        )
    if field_key in DAILY_VISIT_FIELDS:
        return _annual_proxy(value, 365, metric, "daily ridership x 365 days")
    if scene_type == "airport_terminal" and field_key == "terminal_capacity":
        return None
    if scene_type == "stadium" and field_key in PEOPLE_CAPACITY_FIELDS:
        return _annual_proxy(value, 15, metric, "seats/capacity x 15 annual event-equivalents")
    if scene_type == "convention_center":
        if field_key in AREA_FIELDS:
            return _annual_proxy(
                value,
                10,
                metric,
                "exhibition/meeting sqm x 10 visitor-equivalents",
            )
        if field_key in PEOPLE_CAPACITY_FIELDS:
            return _annual_proxy(value, 10, metric, "capacity x 10 annual event-equivalents")
    if scene_type == "luxury_hotel_mice":
        if field_key in ROOM_FIELDS:
            return _annual_proxy(value, 900, metric, "rooms/keys x 900 guest-event-equivalents")
        if field_key in AREA_FIELDS:
            return _annual_proxy(value, 30, metric, "meeting/ballroom sqm x 30 visitor-equivalents")
        if field_key in PEOPLE_CAPACITY_FIELDS:
            return _annual_proxy(value, 120, metric, "ballroom capacity x 120 event-equivalents")
    if scene_type == "mall_mixed_use" and field_key in AREA_FIELDS:
        return _annual_proxy(value, 20, metric, "retail GLA sqm x 20 annual footfall-equivalents")
    if scene_type == "office_government" and field_key in AREA_FIELDS:
        return _annual_proxy(value, 10, metric, "office area sqm x 10 annual user-equivalents")
    if scene_type == "hospital":
        return _annual_proxy(value, 1_000, metric, "hospital scale metric x 1000 annual visits")
    if scene_type == "university":
        return _annual_proxy(value, 180, metric, "campus population/enrollment x 180 active days")
    if scene_type == "transport_hub" and field_key != "line_count":
        return _annual_proxy(value, 365, metric, "transport daily metric x 365 days")
    if scene_type == "cruise_port":
        return AnnualVisitProxy(
            value=value,
            basis=f"direct cruise passenger metric: {field_key}",
            chain=f"{field_key} evidence ({metric.field_value}) -> annual_visits_est={int(value)}",
        )
    if scene_type == "mosque" and field_key in AREA_FIELDS:
        return _annual_proxy(value, 35, metric, "mosque sqm x 35 annual visitor-equivalents")
    return None


def _is_direct_annual_visit_metric(metric: EvidenceMetric | None) -> bool:
    return (
        metric is not None
        and metric.field_key in VISIT_METRIC_FIELDS
        and metric.numeric_unit == "visits/year"
    )


def _annual_proxy(
    value: float,
    multiplier: float,
    metric: EvidenceMetric,
    basis: str,
) -> AnnualVisitProxy:
    annual = value * multiplier
    return AnnualVisitProxy(
        value=annual,
        basis=basis,
        chain=(
            f"{metric.field_key} evidence ({metric.field_value}) -> {basis}; "
            f"{value:g} x {multiplier:g} = annual_visits_est {int(annual)}"
        ),
    )


def _numeric_value_and_unit(field_key: str, value: str) -> tuple[float | None, str | None]:
    lower = value.casefold()
    if field_key == "line_count":
        line_count = _line_count_value(lower)
        return (line_count, "lines") if line_count is not None else (None, None)
    if field_key == "seat_count":
        seats = _seat_count_value(lower)
        return (seats, "seats") if seats is not None else (None, None)
    if field_key == "beds":
        beds = _bed_count_value(lower)
        return (beds, "beds") if beds is not None else (None, None)
    if field_key in FLOOR_COUNT_FIELDS:
        floors = _floor_count_value(lower)
        return (floors, "floors") if floors is not None else (None, None)
    if field_key in HEIGHT_FIELDS:
        height = _height_value(lower)
        return (height, "meters") if height is not None else (None, None)
    if field_key in ROOM_FIELDS:
        rooms = _room_count_value(lower)
        return (rooms, "rooms") if rooms is not None else (None, None)
    if field_key in STAFF_COUNT_FIELDS:
        staff = _staff_count_value(lower)
        return (staff, "people") if staff is not None else (None, None)
    if field_key in AREA_FIELDS:
        area = _area_value(lower, field_key=field_key)
        if area is not None:
            return area, "sqm"
        return None, None
    if field_key in DAILY_VISIT_FIELDS:
        daily_value = _daily_visit_value(lower)
        if daily_value is not None:
            return daily_value, "visits/day"
        return None, None
    if field_key in VISIT_VOLUME_FIELDS:
        annual_numbers = _period_matched_numbers(lower, ANNUAL_PERIOD_MARKERS)
        if annual_numbers:
            return max(annual_numbers), "visits/year"
        daily_value = _daily_visit_value(lower)
        if daily_value is not None:
            return daily_value, "visits/day"
        return None, None
    if field_key in ANNUAL_VISIT_FIELDS:
        annual_numbers = _period_matched_numbers(lower, ANNUAL_PERIOD_MARKERS)
        if annual_numbers:
            return max(annual_numbers), "visits/year"
        daily_value = _daily_visit_value(lower)
        if daily_value is not None:
            return daily_value, "visits/day"
        numbers = _annual_visit_fallback_numbers(lower)
        if numbers and _is_safe_annual_visit_fallback(lower):
            return max(numbers), "visits/year"
        return None, None
    numbers = _numbers_with_multipliers(lower)
    if not numbers:
        return None, None
    number = max(numbers)
    return number, _unit_for_field(field_key, lower)


def _line_count_value(lower: str) -> float | None:
    patterns = [
        r"\bline\s*count\s*[:=]\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:rail/metro\s*)?(?:metro\s*)?(?:rail\s*)?"
        r"(?:lines|routes)\b",
        r"\b(?:serves|served\s+by|connected\s+to|interchange\s+with)\s+"
        r"(\d{1,3})\s*(?:metro\s*)?(?:rail\s*)?(?:lines|routes)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if not match:
            continue
        raw = match.group(1)
        value = _parse_number(raw)
        if value is not None and 1 <= value <= 20:
            return value
    return None


def _floor_count_value(lower: str) -> float | None:
    patterns = [
        r"\b(?:floor\s*count|floors?|storeys?|stories)\s*(?:of|:|=)?\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*(?:floors?|storeys?|stories)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            value = _parse_number(match.group(1))
            if value is not None and 1 <= value <= 200:
                return value
    return None


def _height_value(lower: str) -> float | None:
    patterns = [
        r"\b(?:height|tower\s*height)\s*(?:of|:|=)?\s*(\d[\d,.\s]*\d|\d)\s*(?:m|metres?|meters?)\b",
        r"\b(\d[\d,.\s]*\d|\d)\s*(?:m|metres?|meters?)\s*(?:high|tall|height)\b",
        r"\b(\d[\d,.\s]*\d|\d)\s*-\s*(?:metre|meter|m)\s*(?:high|tall)?\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            value = _parse_number(match.group(1))
            if value is not None and 10 <= value <= 1_000:
                return value
    return None


def _seat_count_value(lower: str) -> float | None:
    values: list[float] = []
    patterns = [
        r"(\d[\d,.\s]*\d|\d)\s*[- ]?(?:seat|seats|seater|spectators|places)\b",
        r"\b(?:capacity|seat\s*count|seating\s*capacity)\s*(?:of|:|=)?\s*"
        r"(\d[\d,.\s]*\d|\d)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            if _is_money_number_context(lower, match.start(), match.end()):
                continue
            local = lower[max(0, match.start() - 24) : min(len(lower), match.end() + 32)]
            if re.search(r"\b(?:sqm|sq\.?\s*m|m2|m²|square\s*met|acre|hectare)\b", local):
                continue
            value = _parse_number(match.group(1))
            if value is not None and not _looks_like_year(match.group(1), value):
                values.append(value)
    return max(values) if values else None


def _bed_count_value(lower: str) -> float | None:
    per_site_patterns = [
        r"(\d[\d,.\s]*\d|\d)\s*(?:inpatient\s*)?beds?\s+per\s+"
        r"(?:facility|hospital|site|regional\s+hospital)",
        r"each\s+(?:modern\s+)?(?:regional\s+)?(?:facility|hospital|site)"
        r".{0,80}?(\d[\d,.\s]*\d|\d)\s*(?:inpatient\s*)?beds?",
    ]
    for pattern in per_site_patterns:
        values = [
            value
            for value in (_parse_number(match.group(1)) for match in re.finditer(pattern, lower))
            if value is not None
        ]
        if values:
            return max(values)
    bed_matches = [
        _parse_number(match.group(1))
        for match in re.finditer(
            r"(\d[\d,.\s]*\d|\d)(?:\s*[-–]\s*|\s*\+?\s*)"
            r"(?:inpatient\s*)?beds?\b",
            lower,
        )
        if not _looks_like_year(match.group(1), _parse_number(match.group(1)) or 0)
    ]
    bed_matches.extend(
        _parse_number(match.group(1))
        for match in re.finditer(
            r"\bbeds?\s*[:=]\s*(\d[\d,.\s]*\d|\d)\b",
            lower,
        )
        if not _looks_like_year(match.group(1), _parse_number(match.group(1)) or 0)
    )
    bed_values = [value for value in bed_matches if value is not None]
    if bed_values:
        return max(bed_values)
    return None


def _room_count_value(lower: str) -> float | None:
    values: list[float] = []
    label_first_patterns = [
        r"(?:guest\s+rooms?|hotel\s+rooms?|rooms?\s+and\s+suites|rooms?|keys)"
        r"\s*[:=]\s*(\d[\d,.\s]*\d|\d)",
        r"(?:lists|offers|features|has|with)\s+(\d[\d,.\s]*\d|\d)\s+"
        r"(?:guest\s+rooms?|hotel\s+rooms?|rooms?\s+and\s+suites|keys)\b",
    ]
    number_first_patterns = [
        r"(\d[\d,.\s]*\d|\d)\s+(?:guest\s+rooms?|hotel\s+rooms?|rooms?\s+and\s+suites|keys)\b",
        r"(\d[\d,.\s]*\d|\d)\s+rooms?\b",
    ]
    for pattern in [*label_first_patterns, *number_first_patterns]:
        for match in re.finditer(pattern, lower):
            if _is_room_count_exclusion_context(
                lower,
                match.start(),
                match.end(),
                match_text=match.group(0),
            ):
                continue
            value = _parse_number(match.group(1))
            if value is None or _looks_like_year(match.group(1), value):
                continue
            if 1 <= value <= 5_000:
                values.append(value)
    if values:
        return max(values)
    return None


def _staff_count_value(lower: str) -> float | None:
    values: list[float] = []
    patterns = [
        r"\b(?:staff|employees?|workforce)\s*(?:count|total)?\s*[:=]\s*"
        r"(\d[\d,.\s]*\d|\d)\b",
        r"\b(\d[\d,.\s]*\d|\d)\s+(?:staff|employees?|workers?)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, lower):
            value = _parse_number(match.group(1))
            if (
                value is not None
                and not _looks_like_year(match.group(1), value)
                and 1 <= value <= 100_000
            ):
                values.append(value)
    return max(values) if values else None


def _is_room_count_exclusion_context(
    lower: str,
    start: int,
    end: int,
    *,
    match_text: str,
) -> bool:
    if re.search(r"\b(?:guest\s+rooms?|hotel\s+rooms?|rooms?\s+and\s+suites|keys)\b", match_text):
        return False
    local = lower[max(0, start - 28) : min(len(lower), end + 32)]
    return bool(
        re.search(
            r"\b(?:meeting|conference|function|ballroom|event|largest|total)\s+rooms?\b"
            r"|\b(?:sq\.?\s*ft|square\s*feet|sqm|m2|m²|square\s*met)",
            local,
        )
    )


def _area_value(lower: str, *, field_key: str | None = None) -> float | None:
    if field_key in {"exhibition_area", "meeting_area"} and re.search(
        r"\b(?:complex|site|campus|plot|land)\s+area\b",
        lower,
    ):
        if not re.search(
            r"\b(?:exhibition|meeting|event|conference|hall|function|ballroom)\s+"
            r"(?:area|space|sqm|sq\.?\s*m|m2|m²)",
            lower,
        ):
            return None
    metric_matches = [
        _parse_number(match.group(1))
        for match in re.finditer(
            r"(\d[\d,.\s]*\d|\d)\s*(?:sqm|sq\.?\s*m|m2|m²|square\s*met(?:er|re)s?)",
            lower,
        )
    ]
    metric_values = [value for value in metric_matches if value is not None]
    if metric_values:
        return max(metric_values)
    sqft_matches = [
        _parse_number(match.group(1))
        for match in re.finditer(r"(\d[\d,.]*)\s*(?:sq\.?\s*ft|square\s*feet|sf)\b", lower)
    ]
    sqft_values = [value * 0.092903 for value in sqft_matches if value is not None]
    if sqft_values:
        return max(sqft_values)
    return None


DAILY_PERIOD_MARKERS = (
    "/day",
    "per day",
    "a day",
    "daily",
    "business day",
    "business-day",
    "weekday",
    "weekdays",
    "passengers/day",
    "passenger/day",
    "riders/day",
    "visits/day",
)
ANNUAL_PERIOD_MARKERS = (
    "/year",
    "per year",
    "a year",
    "this year",
    "annually",
    "annual",
    "yearly",
    "passengers/year",
    "passenger/year",
    "visits/year",
)


def _daily_visit_value(lower: str) -> float | None:
    values: list[float] = []
    preferred_patterns = [
        r"(?:average|avg\.?|typical|normal)\s+(?:daily|business[-\s]+day|weekday)"
        r"(?:\s+(?:visitation|visits|visitors|passengers|ridership|traffic))?"
        r"\s*(?::|=|of)?\s*(\d[\d,.\s]*\d|\d)",
        r"(?:daily|business[-\s]+day|weekday)"
        r"\s+(?:visitation|visits|visitors|passengers|ridership|traffic)"
        r"\s*(?::|=|of)?\s*(\d[\d,.\s]*\d|\d)",
    ]
    for pattern in preferred_patterns:
        for match in re.finditer(pattern, lower):
            value = _parse_number(match.group(1))
            if value is not None and not _looks_like_year(match.group(1), value):
                values.append(value)
    if values:
        return max(values)

    number_first_patterns = [
        r"(\d[\d,.\s]*\d|\d)\s*(?:people|passengers|visitors|visits|riders|users)?"
        r"\s*(?:/|per)\s*(?:business[-\s]+)?day\b",
        r"(\d[\d,.\s]*\d|\d)\s*(?:people|passengers|visitors|visits|riders|users)?"
        r"\s+(?:on|per)\s+(?:business[-\s]+day|weekday|weekdays)\b",
    ]
    for pattern in number_first_patterns:
        for match in re.finditer(pattern, lower):
            if _is_peak_daily_context(lower, match.start(), match.end()):
                continue
            value = _parse_number(match.group(1))
            if value is not None and not _looks_like_year(match.group(1), value):
                values.append(value)
    if values:
        return max(values)

    fallback_values: list[float] = []
    for number, raw, _context, start, end in _numbers_with_context(
        lower,
        include_offsets=True,
    ):
        if _looks_like_year(raw, number):
            continue
        if _is_peak_daily_context(lower, start, end):
            continue
        if _is_annual_number_context(lower, start, end):
            continue
        local = lower[max(0, start - 32) : min(len(lower), end + 32)]
        if re.search(
            r"\b(?:beds?|rooms?|keys|sqm|sq\.?\s*m|m2|m²|square\s*met|"
            r"staff|doctors?|employees?)\b",
            local,
        ):
            continue
        if any(marker in local for marker in DAILY_PERIOD_MARKERS) and _has_visit_context(local):
            fallback_values.append(number)
    return max(fallback_values) if fallback_values else None


def _is_peak_daily_context(lower: str, start: int, end: int) -> bool:
    local = lower[max(0, start - 36) : min(len(lower), end + 48)]
    return bool(
        re.search(
            r"\b(?:special\s+dates?|peak|peaks?|maximum|max\.?|record|holiday|holidays)\b",
            local,
        )
    )


def _is_annual_number_context(lower: str, start: int, end: int) -> bool:
    local = lower[max(0, start - 32) : min(len(lower), end + 32)]
    return bool(
        re.search(
            r"\b(?:annual|annually|yearly|per\s+year|passengers/year|visits/year)\b",
            local,
        )
    )


def _period_matched_numbers(lower: str, markers: tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for number, raw, context in _numbers_with_context(lower):
        if _looks_like_year(raw, number):
            continue
        if any(marker in context for marker in markers):
            values.append(number)
    return values


def _period_matched_visit_numbers(lower: str, markers: tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for number, raw, context in _numbers_with_context(lower):
        if _looks_like_year(raw, number):
            continue
        if any(marker in context for marker in markers) and _has_visit_context(context):
            values.append(number)
    return values


def _has_visit_context(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:passengers?|visitors?|visits?|footfall|attendance|attendees?|"
            r"ridership|riders?|users?|people|consultations?|outpatients?|"
            r"patients?|boardings?|traffic)\b",
            lower,
        )
    )


def _numbers_with_multipliers(lower: str) -> list[float]:
    return [
        number
        for number, raw, _ in _numbers_with_context(lower)
        if not _looks_like_year(raw, number)
    ]


def _annual_visit_fallback_numbers(lower: str) -> list[float]:
    values: list[float] = []
    for number, raw, _context, start, end in _numbers_with_context(
        lower,
        include_offsets=True,
    ):
        if _looks_like_year(raw, number):
            continue
        local = lower[max(0, start - 40) : min(len(lower), end + 40)]
        if not _has_visit_context(local):
            continue
        if re.search(
            r"\b(?:runways?|length|height|wide|long|floors?|storeys?|stories)\b"
            r"|\s+m(?:[\s.,;:)]|$)|\b(?:meters?|metres?|kilometers?|kilometres?)\b",
            local,
        ):
            continue
        values.append(number)
    return values


def _numbers_with_context(
    lower: str,
    *,
    include_offsets: bool = False,
) -> list[Any]:
    values = []
    for match in re.finditer(r"\d[\d,.]*(?:e[+-]?\d+)?", lower):
        if match.start() > 0 and lower[match.start() - 1].isalpha():
            continue
        suffix = lower[match.end() : match.end() + 24]
        context = _number_clause_context(lower, match.start(), match.end())
        compact_million = _has_compact_million_suffix(suffix, context)
        if _is_money_number_context(lower, match.start(), match.end()):
            continue
        number = _parse_number(
            match.group(0),
            scaled_suffix=(
                "billion" in suffix or "million" in suffix or compact_million
            ),
        )
        if number is None:
            continue
        if "billion" in suffix:
            number *= 1_000_000_000
        elif "million" in suffix or compact_million:
            number *= 1_000_000
        if include_offsets:
            values.append((number, match.group(0), context, match.start(), match.end()))
        else:
            values.append((number, match.group(0), context))
    return values


def _is_safe_annual_visit_fallback(lower: str) -> bool:
    if not _has_visit_context(lower):
        return False
    return not bool(
        re.search(
            r"\b(?:destinations?|airlines?|routes?|traffic\s+share|market\s+share)\b"
            r"|%",
            lower,
        )
    )


def _has_compact_million_suffix(suffix: str, context: str) -> bool:
    if not re.match(r"\s*m(?:\b|/yr\b|/year\b|/annum\b|/pa\b|/p\.a\.)", suffix):
        return False
    if re.match(r"\s*m(?:2|²|eters?\b|etres?\b)", suffix):
        return False
    if re.match(r"\s+m\b", suffix) and not re.match(
        r"\s+m\s*(?:/yr\b|/year\b|/annum\b|/pa\b|/p\.a\.\b|"
        r"passengers?\b|visitors?\b|visits?\b|people\b|users?\b|riders?\b)",
        suffix,
    ):
        return False
    return bool(
        re.search(r"\bm\s*(?:/yr|/year|/annum|/pa|/p\.a\.)\b", suffix)
        or _has_visit_context(context)
    )


def _is_money_number_context(lower: str, start: int, end: int) -> bool:
    left = max(lower.rfind(delimiter, 0, start) for delimiter in (";", "\n", "."))
    right_candidates = [
        position
        for position in (lower.find(delimiter, end) for delimiter in (";", "\n", "."))
        if position != -1
    ]
    right = min(right_candidates) if right_candidates else len(lower)
    local = lower[max(0, left + 1) : right]
    if re.search(r"(?:us\$|\$|€|£|\busd\b|\beur\b|\bgbp\b)", local):
        return True
    return bool(
        "million" in local
        and re.search(
            r"\b(?:renovation|agreement|contract|investment|cost|funding|financing)\b",
            local,
        )
    )


def _number_clause_context(lower: str, start: int, end: int) -> str:
    left = max(lower.rfind(delimiter, 0, start) for delimiter in (";", "\n", "."))
    right_candidates = [
        position
        for position in (lower.find(delimiter, end) for delimiter in (";", "\n", "."))
        if position != -1
    ]
    right = min(right_candidates) if right_candidates else len(lower)
    return lower[left + 1 : right]


def _looks_like_year(raw: str, number: float) -> bool:
    stripped = raw.strip(".,")
    return stripped.isdigit() and len(stripped) == 4 and 1900 <= number <= 2100


def _field_key(row: dict[str, Any]) -> str:
    for value in [row.get("field_group"), row.get("indicator_name")]:
        key = _normalize_key(str(value or ""))
        if key:
            return key
    return ""


def _unit_for_field(field_key: str, lower: str) -> str | None:
    if field_key == "beds":
        return "beds"
    if field_key in PEOPLE_CAPACITY_FIELDS:
        return "people" if field_key != "seat_count" else "seats"
    if field_key in FLOOR_COUNT_FIELDS:
        return "floors"
    if field_key in HEIGHT_FIELDS:
        return "meters"
    if field_key in ROOM_FIELDS:
        return "rooms"
    if field_key in ANNUAL_VISIT_FIELDS:
        return "visits/year"
    if field_key in DAILY_VISIT_FIELDS:
        return "visits/day"
    if "%" in lower or "share" in field_key:
        return "percent"
    return None


def _parse_number(value: str, *, scaled_suffix: bool = False) -> float | None:
    normalized = value.replace(" ", "")
    if (
        scaled_suffix
        and "," not in normalized
        and re.fullmatch(r"\d{1,3}\.\d{1,3}", normalized)
    ):
        try:
            return float(normalized)
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", normalized):
        normalized = normalized.replace(".", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", normalized):
        normalized = normalized.replace(",", "")
    try:
        return float(normalized.replace(",", ""))
    except ValueError:
        return None


def _valid_choice(value: Any, allowed: set[str], fallback: str) -> str:
    text_value = str(value or "")
    return text_value if text_value in allowed else fallback


def _clean_optional_text(value: Any) -> str | None:
    text_value = re.sub(r"\s+", " ", str(value or "")).strip()
    return text_value or None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _has_digit(value: str) -> bool:
    return bool(re.search(r"\d", value))


def _domain(url: str | None) -> str:
    value = str(url or "").strip().casefold()
    if not value:
        return ""
    match = re.match(r"https?://([^/]+)", value)
    domain = match.group(1) if match else value.split("/", 1)[0]
    return domain[4:] if domain.startswith("www.") else domain


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_label(field_key: str) -> str:
    return str(field_key).replace("_", " ").title()


def _packet_hash(packet: dict[str, Any]) -> str:
    payload = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_package_hash(
    target: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> str:
    payload = {
        "property": {
            "property_id": target["property_id"],
            "scene_type": target["scene_type"],
        },
        "evidence_items": sorted(
            [
                {
                    "field_group": row.get("field_group"),
                    "indicator_name": row.get("indicator_name"),
                    "field_value": row.get("field_value"),
                    "unit": row.get("unit"),
                    "evidence_type": row.get("evidence_type"),
                    "source_name": row.get("source_name"),
                    "source_tier": row.get("source_tier"),
                    "source_url": row.get("source_url"),
                    "source_date": row.get("source_date"),
                }
                for row in evidence_rows
            ],
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
    }
    text_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text_payload.encode("utf-8")).hexdigest()


def _legacy_evidence_package_hash(
    target: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
    *,
    city: str | None = None,
) -> str:
    payload = {
        "property": {
            "property_id": target["property_id"],
            "country": target["country"],
            "city": city if city is not None else target["city"],
            "property_name": target["canonical_name"],
            "scene_type": target["scene_type"],
        },
        "evidence_items": sorted(
            [
                {
                    "field_group": row.get("field_group"),
                    "indicator_name": row.get("indicator_name"),
                    "field_value": row.get("field_value"),
                    "unit": row.get("unit"),
                    "evidence_type": row.get("evidence_type"),
                    "source_name": row.get("source_name"),
                    "source_tier": row.get("source_tier"),
                    "source_url": row.get("source_url"),
                    "source_date": row.get("source_date"),
                }
                for row in evidence_rows
            ],
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        ),
    }
    text_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text_payload.encode("utf-8")).hexdigest()


_GPT_SYSTEM_PROMPT = """
You are the iSite2 scene-modeling and conclusion agent.

Use only the objective evidence_items supplied in the user JSON. Do not invent facts,
do not browse, and do not treat descriptive role evidence as a primary metric. In
particular, airport gateway_role / hub_role / strategic_role evidence is not a hard
primary metric. A hard primary metric must be quantitative and must match the
scene's quantitative_primary_indicators.

Aggregate all evidence for the property, choose the strongest objective primary
metric, derive capacity and annual visit estimates conservatively, and produce
Chinese recommendation and next-action text. Keep the high-value opportunity chain
separate from indoor build-status evidence.

Return one JSON object with these keys:
metric_availability_level, selected_primary_metric, annual_visits_est,
annual_visits_basis, capacity_estimate, capacity_unit, capacity_basis,
evidence_status, value_class, action_class, recommended_solution,
reason_to_recommend, next_action, inference_basis, inference_chain,
inference_confidence, assumption_note, review_items.

selected_primary_metric should be null when no hard quantitative primary metric is
present. review_items should contain concrete QA actions when evidence is weak,
single-source, conflicting, or missing primary metrics.
""".strip()

_CODEX_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_availability_level": {"type": "string"},
        "selected_primary_metric": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "field_group": {"type": "string"},
                        "field_value": {"type": "string"},
                        "source_urls": {"type": "array", "items": {"type": "string"}},
                        "numeric_value": {"type": ["number", "null"]},
                        "unit": {"type": ["string", "null"]},
                    },
                    "required": [
                        "field_group",
                        "field_value",
                        "source_urls",
                        "numeric_value",
                        "unit",
                    ],
                    "additionalProperties": False,
                },
            ]
        },
        "annual_visits_est": {"type": ["number", "null"]},
        "annual_visits_basis": {"type": ["string", "null"]},
        "capacity_estimate": {"type": ["number", "null"]},
        "capacity_unit": {"type": ["string", "null"]},
        "capacity_basis": {"type": ["string", "null"]},
        "evidence_status": {"type": "string"},
        "value_class": {"type": "string"},
        "action_class": {"type": "string"},
        "recommended_solution": {"type": "string"},
        "reason_to_recommend": {"type": "string"},
        "next_action": {"type": "string"},
        "inference_basis": {"type": "string"},
        "inference_chain": {"type": "string"},
        "inference_confidence": {"type": "string"},
        "assumption_note": {"type": ["string", "null"]},
        "review_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "next_action": {"type": "string"},
                },
                "required": ["reason", "next_action"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "metric_availability_level",
        "selected_primary_metric",
        "annual_visits_est",
        "annual_visits_basis",
        "capacity_estimate",
        "capacity_unit",
        "capacity_basis",
        "evidence_status",
        "value_class",
        "action_class",
        "recommended_solution",
        "reason_to_recommend",
        "next_action",
        "inference_basis",
        "inference_chain",
        "inference_confidence",
        "assumption_note",
        "review_items",
    ],
    "additionalProperties": False,
}
