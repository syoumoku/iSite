from __future__ import annotations

from datetime import UTC, datetime, timedelta

from isite2.growth.network_signals import (
    ComplaintObservationInput,
    NetworkPerformanceSummary,
    aggregate_property_complaints,
    classify_network_complaint,
    match_property_complaint,
    network_validation_priority,
    sanitize_complaint_text,
)


def test_complaint_sanitization_removes_personal_identifiers() -> None:
    text = "Maria @maria call me +55 11 99999-8888 at maria@example.com: sem sinal 4G."

    sanitized = sanitize_complaint_text(text)

    assert "Maria" not in sanitized
    assert "@maria" not in sanitized
    assert "99999" not in sanitized
    assert "example.com" not in sanitized
    assert "sem sinal 4G" in sanitized


def test_complaint_classifier_keeps_network_pain_and_rejects_billing() -> None:
    assert classify_network_complaint("Sem sinal 4G dentro do shopping.") == (
        "no_signal",
        1.0,
    )
    assert classify_network_complaint("Internet móvel muito lenta no aeroporto.") == (
        "slow_data",
        0.6,
    )
    assert classify_network_complaint("Cobrança indevida na fatura do hotel.") is None
    assert classify_network_complaint("O Wi-Fi gratuito do hotel não funciona.") is None


def test_property_match_requires_name_and_city_context() -> None:
    assert match_property_complaint(
        "Sem sinal no Shopping Center Norte em São Paulo",
        canonical_name="Shopping Center Norte",
        aliases=["Center Norte"],
        city="São Paulo",
        country="Brazil",
    ) == "exact"
    assert match_property_complaint(
        "Sem sinal no Center Norte em Recife",
        canonical_name="Shopping Center Norte",
        aliases=["Center Norte"],
        city="São Paulo",
        country="Brazil",
    ) is None


def test_complaint_rollup_requires_three_recent_direct_observations() -> None:
    now = datetime.now(UTC)
    observations = [
        ComplaintObservationInput(
            category="weak_signal",
            severity_weight=0.8,
            observed_at=now - timedelta(days=index),
            source_url=f"https://example.test/review/{index}",
            content_hash=f"hash-{index}",
        )
        for index in range(2)
    ]

    rollup = aggregate_property_complaints(observations, now=now)

    assert rollup.pressure_level == "insufficient"
    assert rollup.valid_complaint_count == 2


def test_network_priority_never_claims_build_status() -> None:
    priority = network_validation_priority(
        value_class="National Flagship",
        complaint_pressure="high",
        mobile=NetworkPerformanceSummary(
            performance_class="poor",
            confidence="high",
        ),
        fixed=None,
    )

    assert priority.priority == "High"
    assert "RF" in priority.next_action
    assert priority.build_status_changed is False
