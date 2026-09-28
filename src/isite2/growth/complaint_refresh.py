from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from isite2.db.models import (
    ComplaintObservationDB,
    PropertyAliasDB,
    PropertyComplaintRollupDB,
    PropertyDB,
)
from isite2.db.session import create_session_factory
from isite2.growth.network_signals import (
    ComplaintObservationInput,
    aggregate_property_complaints,
    classify_network_complaint,
    complaint_content_hash,
    match_property_complaint,
    sanitize_complaint_text,
)

COMPLAINT_CATEGORIES = {
    "no_signal": 1.0,
    "network_outage": 1.0,
    "dropped_call": 0.8,
    "weak_signal": 0.8,
    "slow_data": 0.6,
}


class AmbiguousComplaintClassifier(Protocol):
    provider_name: str

    def classify(self, sanitized_text: str) -> tuple[str, float, float] | None:
        ...


class CodexOAuthComplaintClassifier:
    provider_name = "codex_oauth"

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120,
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
        self.model = model or os.getenv("ISITE2_CODEX_OAUTH_MODEL") or "gpt-5.4-mini"
        self.timeout_seconds = timeout_seconds

    def classify(self, sanitized_text: str) -> tuple[str, float, float] | None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["relevant", "category", "confidence"],
            "properties": {
                "relevant": {"type": "boolean"},
                "category": {
                    "type": ["string", "null"],
                    "enum": [*COMPLAINT_CATEGORIES, None],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }
        prompt = (
            "Classify this sanitized public text as a cellular-network complaint. "
            "Only 4G/5G/mobile no-signal, outage, dropped-call, weak-signal, or "
            "slow-data issues are relevant. Exclude billing, customer service, "
            "hotel service, and Wi-Fi-only issues. Do not infer missing facts.\n\n"
            f"TEXT:\n{sanitized_text[:4000]}"
        )
        with tempfile.TemporaryDirectory(prefix="isite2_complaint_") as directory:
            temp_dir = Path(directory)
            schema_path = temp_dir / "schema.json"
            output_path = temp_dir / "decision.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            process = subprocess.run(
                [
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
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "-C",
                    str(temp_dir),
                    "-",
                ],
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if process.returncode != 0 or not output_path.exists():
                raise RuntimeError("Codex OAuth complaint classification failed")
            decision = json.loads(output_path.read_text(encoding="utf-8"))
        category = decision.get("category")
        if not decision.get("relevant") or category not in COMPLAINT_CATEGORIES:
            return None
        return (
            str(category),
            COMPLAINT_CATEGORIES[str(category)],
            float(decision.get("confidence") or 0),
        )


class FileCachedComplaintClassifier:
    provider_name = "file_cached_codex_oauth"

    def __init__(
        self,
        provider: AmbiguousComplaintClassifier,
        cache_dir: Path = Path("outputs/complaint_refresh/gpt_cache"),
    ) -> None:
        self.provider = provider
        self.cache_dir = cache_dir

    def classify(self, sanitized_text: str) -> tuple[str, float, float] | None:
        text_hash = hashlib.sha256(sanitized_text.encode()).hexdigest()
        path = self.cache_dir / f"{text_hash}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not payload.get("relevant"):
                return None
            return (
                str(payload["category"]),
                float(payload["severity_weight"]),
                float(payload["confidence"]),
            )
        result = self.provider.classify(sanitized_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            {"relevant": False}
            if result is None
            else {
                "relevant": True,
                "category": result[0],
                "severity_weight": result[1],
                "confidence": result[2],
            }
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result


def refresh_property_complaints(
    engine: Engine,
    records: list[dict],
    *,
    source_policy_path: Path = Path("config/complaint_sources.yaml"),
    source_manifest_path: str | None = None,
    now: datetime | None = None,
    ambiguous_classifier: AmbiguousComplaintClassifier | None = None,
) -> dict:
    """Ingest compliant, property-matched complaints and refresh public-safe rollups."""
    reference = now or datetime.now(UTC)
    policy = load_complaint_source_policy(source_policy_path)
    session_factory = create_session_factory(engine)
    counts: Counter[str] = Counter()
    touched: set[str] = set()
    review_items: list[dict[str, str]] = []
    with session_factory.begin() as session:
        properties = session.scalars(select(PropertyDB)).all()
        aliases = defaultdict(list)
        for alias in session.scalars(select(PropertyAliasDB)).all():
            aliases[str(alias.property_id)].append(alias.alias)
        properties_by_id = {str(row.id): row for row in properties}

        for record in records:
            source_url = str(record.get("source_url") or "").strip()
            if not source_url or not source_is_allowed(source_url, record, policy):
                counts["source_policy_rejected"] += 1
                continue
            raw_text = str(record.get("text") or record.get("sanitized_text") or "").strip()
            sanitized = sanitize_complaint_text(raw_text)
            classified = classify_network_complaint(sanitized)
            classification_method = "rule"
            classification_confidence = None
            if (
                classified is None
                and ambiguous_classifier is not None
                and record.get("ambiguous_network_candidate") is True
            ):
                try:
                    ambiguous = ambiguous_classifier.classify(sanitized)
                except Exception:  # noqa: BLE001 - keep the rest of the batch
                    counts["ambiguous_provider_error"] += 1
                    continue
                if ambiguous is not None:
                    classified = (ambiguous[0], ambiguous[1])
                    classification_method = ambiguous_classifier.provider_name
                    classification_confidence = ambiguous[2]
                    counts["ambiguous_provider_accepted"] += 1
            if classified is None:
                counts["irrelevant_or_ambiguous"] += 1
                continue
            property_row, match_status = _match_record_property(
                record,
                sanitized,
                properties,
                properties_by_id,
                aliases,
            )
            if property_row is None:
                counts["property_match_review"] += 1
                review_items.append(
                    {
                        "source_url": source_url,
                        "reason": "Property name/city context did not resolve uniquely.",
                    }
                )
                continue
            category, severity_weight = classified
            content_hash = complaint_content_hash(sanitized, source_url)
            observed_at = _parse_datetime(record.get("observed_at"), reference)
            observation = ComplaintObservationDB(
                id=str(uuid4()),
                property_id=str(property_row.id),
                country=property_row.country,
                city=property_row.city,
                source_name=str(record.get("source_name") or urlparse(source_url).hostname or ""),
                source_url=source_url,
                source_domain=str(urlparse(source_url).hostname or "").casefold(),
                observed_at=observed_at,
                fetched_at=reference,
                sanitized_text=sanitized,
                content_hash=content_hash,
                category=category,
                severity_weight=severity_weight,
                match_status=match_status,
                classification_method=classification_method,
                classification_confidence=(
                    classification_confidence
                    if classification_confidence is not None
                    else 1.0
                    if severity_weight >= 0.8
                    else 0.9
                ),
                source_manifest_path=source_manifest_path,
            )
            try:
                with session.begin_nested():
                    session.add(observation)
                    session.flush()
                counts["observations_inserted"] += 1
                touched.add(str(property_row.id))
            except IntegrityError:
                counts["duplicates_suppressed"] += 1

    rollup_summary = (
        refresh_complaint_rollups(
            engine,
            property_ids=sorted(touched),
            now=reference,
        )
        if touched
        else {"property_count": 0, "percentile_updates": 0}
    )
    return {
        "mode": "property_complaint_refresh",
        "timestamp_utc": reference.isoformat(),
        "counts": dict(counts),
        "touched_property_count": len(touched),
        "rollups": rollup_summary,
        "review_items": review_items[:50],
        "public_raw_text": False,
    }


def refresh_complaint_rollups(
    engine: Engine,
    *,
    property_ids: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    reference = now or datetime.now(UTC)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        statement = select(PropertyDB)
        if property_ids:
            statement = statement.where(PropertyDB.id.in_(property_ids))
        properties = session.scalars(statement).all()
        for property_row in properties:
            rows = session.scalars(
                select(ComplaintObservationDB).where(
                    ComplaintObservationDB.property_id == str(property_row.id)
                )
            ).all()
            inputs = [
                ComplaintObservationInput(
                    category=row.category,
                    severity_weight=row.severity_weight,
                    observed_at=row.observed_at,
                    source_url=row.source_url,
                    content_hash=row.content_hash,
                )
                for row in rows
            ]
            rollup = aggregate_property_complaints(inputs, now=reference)
            input_hash = _rollup_hash(rows, rollup.period_days)
            existing = session.scalar(
                select(PropertyComplaintRollupDB).where(
                    PropertyComplaintRollupDB.property_id == str(property_row.id),
                    PropertyComplaintRollupDB.period_days == rollup.period_days,
                    PropertyComplaintRollupDB.input_hash == input_hash,
                )
            )
            if existing is None:
                session.add(
                    PropertyComplaintRollupDB(
                        id=str(uuid4()),
                        property_id=str(property_row.id),
                        period_days=rollup.period_days,
                        input_hash=input_hash,
                        valid_complaint_count=rollup.valid_complaint_count,
                        weighted_complaint_count=rollup.weighted_complaint_count,
                        source_count=rollup.source_count,
                        category_counts=rollup.category_counts,
                        pressure_level=rollup.pressure_level,
                        confidence=rollup.confidence,
                        latest_observed_at=max(
                            (row.observed_at for row in rows),
                            default=None,
                        ),
                        calculated_at=reference,
                    )
                )

    percentile_updates = _apply_country_scene_percentiles(engine)
    return {
        "property_count": len(properties),
        "percentile_updates": percentile_updates,
    }


def load_complaint_source_policy(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def source_is_allowed(source_url: str, record: dict, policy: dict) -> bool:
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    lowered = source_url.casefold()
    if any(
        str(pattern).casefold() in lowered
        for pattern in policy.get("rejected_source_patterns", [])
    ):
        return False
    host = parsed.hostname.casefold()
    source_policy = next(
        (
            item
            for item in policy.get("property_level_sources", [])
            if isinstance(item, dict)
            and bool(str(item.get("domain") or "").strip())
            and (
                host == str(item.get("domain") or "").casefold()
                or host.endswith(f".{str(item.get('domain') or '').casefold()}")
            )
        ),
        None,
    )
    if source_policy is None:
        return False
    approved_policy = (
        source_policy.get("approval_status") == "approved"
        and source_policy.get("automation_allowed") is True
        and source_policy.get("robots_allowed") is True
        and source_policy.get("terms_allow_automation") is True
        and source_policy.get("no_login_required") is True
        and bool(source_policy.get("last_verified_at"))
        and bool(source_policy.get("verification_url"))
    )
    retained_record_checks = all(
        record.get(key) is True
        for key in ("robots_allowed", "terms_allow_automation", "no_login_required")
    )
    return approved_policy and retained_record_checks


def _match_record_property(
    record: dict,
    text: str,
    properties: list[PropertyDB],
    properties_by_id: dict[str, PropertyDB],
    aliases: dict[str, list[str]],
) -> tuple[PropertyDB | None, str]:
    property_id = str(record.get("property_id") or "")
    candidates = [properties_by_id[property_id]] if property_id in properties_by_id else properties
    matches: list[PropertyDB] = []
    for row in candidates:
        match = match_property_complaint(
            text,
            canonical_name=row.canonical_name,
            aliases=aliases.get(str(row.id), []),
            city=row.city,
            country=row.country,
        )
        if match:
            matches.append(row)
    if len(matches) == 1:
        return matches[0], "exact"
    return None, "possible_duplicate" if len(matches) > 1 else "unmatched"


def _apply_country_scene_percentiles(engine: Engine) -> int:
    session_factory = create_session_factory(engine)
    updates = 0
    with session_factory.begin() as session:
        properties = {str(row.id): row for row in session.scalars(select(PropertyDB)).all()}
        rollups = session.scalars(
            select(PropertyComplaintRollupDB).order_by(
                PropertyComplaintRollupDB.calculated_at.desc()
            )
        ).all()
        latest: dict[str, PropertyComplaintRollupDB] = {}
        for rollup in rollups:
            latest.setdefault(str(rollup.property_id), rollup)
        groups: dict[tuple[str, str], list[PropertyComplaintRollupDB]] = defaultdict(list)
        for property_id, rollup in latest.items():
            property_row = properties.get(property_id)
            if property_row is not None and rollup.valid_complaint_count >= 3:
                groups[(property_row.country, property_row.scene_type)].append(rollup)
        for group in groups.values():
            ordered = sorted(group, key=lambda row: row.weighted_complaint_count)
            denominator = max(1, len(ordered) - 1)
            for rank, rollup in enumerate(ordered):
                percentile = rank / denominator if len(ordered) > 1 else 0.5
                rollup.pressure_percentile = percentile
                rollup.pressure_level = (
                    "high" if percentile >= 0.8 else "medium" if percentile >= 0.6 else "low"
                )
                updates += 1
    return updates


def _rollup_hash(rows: list[ComplaintObservationDB], period_days: int) -> str:
    payload = {
        "period_days": period_days,
        "content_hashes": sorted(row.content_hash for row in rows),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _parse_datetime(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
