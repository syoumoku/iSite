from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

PERSONAL_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_.-]+")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)")
LEADING_NAME_RE = re.compile(r"^\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÖØ-öø-ÿ'-]{2,}\s+(?=@)")

EXCLUDED_MARKERS = {
    "billing",
    "invoice",
    "charged",
    "charge",
    "cobranca",
    "cobrança",
    "fatura",
    "atendimento",
    "customer service",
    "room service",
    "hotel service",
}
CELLULAR_MARKERS = {
    "4g",
    "5g",
    "celular",
    "mobile",
    "movel",
    "móvel",
    "sinal",
    "signal",
    "dados",
    "data",
    "ligacao",
    "ligação",
    "call",
}
CATEGORY_RULES: list[tuple[str, float, tuple[str, ...]]] = [
    (
        "no_signal",
        1.0,
        (
            "sem sinal",
            "no signal",
            "sem rede",
            "no service",
            "fora de cobertura",
        ),
    ),
    (
        "network_outage",
        1.0,
        (
            "rede caiu",
            "network outage",
            "indisponivel",
            "indisponível",
            "servico fora",
            "serviço fora",
        ),
    ),
    (
        "dropped_call",
        0.8,
        (
            "queda de chamada",
            "queda de ligacao",
            "queda de ligação",
            "dropped call",
            "call drops",
        ),
    ),
    (
        "weak_signal",
        0.8,
        (
            "sinal fraco",
            "weak signal",
            "poor signal",
            "baixa cobertura",
        ),
    ),
    (
        "slow_data",
        0.6,
        (
            "internet lenta",
            "internet movel muito lenta",
            "dados lentos",
            "dados moveis lentos",
            "slow data",
            "slow internet",
            "velocidade baixa",
        ),
    ),
]


@dataclass(frozen=True)
class ComplaintObservationInput:
    category: str
    severity_weight: float
    observed_at: datetime
    source_url: str
    content_hash: str


@dataclass(frozen=True)
class ComplaintRollup:
    valid_complaint_count: int
    weighted_complaint_count: float
    source_count: int
    category_counts: dict[str, int]
    pressure_level: str
    confidence: str
    period_days: int = 365


@dataclass(frozen=True)
class NetworkPerformanceSummary:
    performance_class: str
    confidence: str
    period: str | None = None


@dataclass(frozen=True)
class NetworkValidationDecision:
    priority: str
    reasons: list[str] = field(default_factory=list)
    next_action: str = ""
    build_status_changed: bool = False


def sanitize_complaint_text(text: str) -> str:
    sanitized = LEADING_NAME_RE.sub("", str(text or ""))
    sanitized = PERSONAL_EMAIL_RE.sub("[redacted-email]", sanitized)
    sanitized = HANDLE_RE.sub("[redacted-handle]", sanitized)
    sanitized = PHONE_RE.sub("[redacted-phone]", sanitized)
    return re.sub(r"\s+", " ", sanitized).strip()


def complaint_content_hash(text: str, source_url: str = "") -> str:
    normalized = _normalize(sanitize_complaint_text(text))
    return hashlib.sha256(f"{source_url}|{normalized}".encode()).hexdigest()


def classify_network_complaint(text: str) -> tuple[str, float] | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if any(marker in normalized for marker in EXCLUDED_MARKERS):
        return None
    if "wi fi" in normalized or "wifi" in normalized:
        if not any(marker in normalized for marker in CELLULAR_MARKERS - {"dados", "data"}):
            return None
    for category, weight, markers in CATEGORY_RULES:
        if any(_normalize(marker) in normalized for marker in markers):
            return category, weight
    return None


def match_property_complaint(
    text: str,
    *,
    canonical_name: str,
    aliases: list[str],
    city: str,
    country: str,
) -> str | None:
    normalized = _normalize(text)
    names = [canonical_name, *aliases]
    name_hit = any(
        len(name_normalized) >= 4 and name_normalized in normalized
        for name_normalized in (_normalize(name) for name in names)
    )
    if not name_hit:
        return None
    context_values = [_normalize(city), _normalize(country)]
    if not any(value and value in normalized for value in context_values):
        return None
    return "exact"


def aggregate_property_complaints(
    observations: list[ComplaintObservationInput],
    *,
    now: datetime | None = None,
) -> ComplaintRollup:
    reference = now or datetime.now(UTC)
    seen: set[str] = set()
    valid: list[ComplaintObservationInput] = []
    for observation in sorted(observations, key=lambda item: item.observed_at, reverse=True):
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age_days = max(0, (reference - observed_at).days)
        if age_days > 365 or observation.content_hash in seen:
            continue
        seen.add(observation.content_hash)
        valid.append(observation)

    weighted = 0.0
    category_counts: dict[str, int] = {}
    sources: set[str] = set()
    for observation in valid:
        observed_at = observation.observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        age_days = max(0, (reference - observed_at).days)
        freshness = 1.0 if age_days <= 90 else 0.6 if age_days <= 180 else 0.3
        weighted += max(0.0, observation.severity_weight) * freshness
        category_counts[observation.category] = category_counts.get(observation.category, 0) + 1
        host = urlparse(observation.source_url).hostname
        if host:
            sources.add(host.casefold())

    count = len(valid)
    if count < 3:
        pressure = "insufficient"
        confidence = "insufficient"
    elif count >= 5 and weighted >= 4:
        pressure = "high"
        confidence = "high" if len(sources) >= 2 else "medium"
    elif weighted >= 2:
        pressure = "medium"
        confidence = "medium"
    else:
        pressure = "low"
        confidence = "low"
    return ComplaintRollup(
        valid_complaint_count=count,
        weighted_complaint_count=round(weighted, 4),
        source_count=len(sources),
        category_counts=category_counts,
        pressure_level=pressure,
        confidence=confidence,
    )


def network_validation_priority(
    *,
    value_class: str,
    complaint_pressure: str | None,
    mobile: NetworkPerformanceSummary | None,
    fixed: NetworkPerformanceSummary | None,
) -> NetworkValidationDecision:
    reasons: list[str] = []
    if complaint_pressure == "high":
        reasons.append("high property-level network complaint pressure")
    for label, summary in (("mobile", mobile), ("fixed", fixed)):
        if (
            summary is not None
            and summary.performance_class == "poor"
            and summary.confidence in {"high", "medium"}
        ):
            reasons.append(f"{label} Ookla tile performance is poor")
    high_value = value_class in {"National Flagship", "City Core"}
    if reasons and high_value:
        priority = "High"
    elif reasons:
        priority = "Medium"
    else:
        priority = "Normal"
    next_action = (
        "Schedule a property-level indoor RF survey and verify operator-specific "
        "coverage, capacity, and busy-hour performance."
        if reasons
        else "Continue normal evidence refresh; no network-pain escalation is justified."
    )
    return NetworkValidationDecision(
        priority=priority,
        reasons=reasons,
        next_action=next_action,
        build_status_changed=False,
    )


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", normalized.casefold())
    return re.sub(r"\s+", " ", normalized).strip()
