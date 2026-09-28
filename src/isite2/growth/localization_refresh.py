from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from isite2.localization import (
    DatabaseLocalizationCache,
    LocalizedTextResult,
    TranslationProvider,
    apply_localization_glossary,
    detect_source_locale,
    is_usable_localized_text_result,
    is_usable_translation,
    localize_text,
    primary_metric_display_source_text,
    resolve_locale,
    source_text_hash,
    supported_locales,
    translatable_latin_tokens,
    translation_provider_from_mode,
)

DEFAULT_LOCALIZATION_TEXT_KINDS = (
    "reason_to_recommend",
    "next_action",
    "primary_metric.display_text",
    "evidence.field_value",
    "inference.inferred_value",
    "inference.inference_basis",
    "inference.inference_chain",
    "review.reason",
    "review.next_action",
)


@dataclass(frozen=True)
class LocalizationTextCandidate:
    property_id: str
    text_kind: str
    source_text: str


def refresh_localization_cache_for_packets(
    packets: Iterable,
    engine: Engine,
    *,
    locales: Sequence[str] | None = None,
    text_kinds: Sequence[str] | None = None,
    provider_mode: str = "codex-oauth",
    provider: TranslationProvider | None = None,
    batch_size: int | None = None,
    batch_concurrency: int = 1,
    single_fallback: bool = True,
    limit: int = 0,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Pre-warm localized free-text cache for UI/export surfaces.

    GET endpoints intentionally do not call GPT. This hook makes post-scan and
    explicit refreshes populate missing translations ahead of display time.
    """
    resolved_locales = _resolved_locales(locales)
    wanted_kinds = tuple(text_kinds or DEFAULT_LOCALIZATION_TEXT_KINDS)
    candidates = list(text_candidates_from_packets(packets, text_kinds=wanted_kinds))
    if limit > 0:
        candidates = candidates[:limit]

    cache = DatabaseLocalizationCache(engine)
    exact_cache, reusable_cache = _cache_indexes(cache.load_results())
    provider = None if dry_run else provider or translation_provider_from_mode(provider_mode)
    resolved_batch_size = _resolved_batch_size(batch_size)
    counts: Counter[str] = Counter()
    missing_by_text_kind: Counter[str] = Counter()
    affected_property_ids: list[str] = []
    affected_seen: set[str] = set()
    provider_group_seen: set[tuple[str, str, str]] = set()
    errors: list[dict[str, str]] = []

    for locale in resolved_locales:
        pending: list[tuple[LocalizationTextCandidate, str]] = []
        for candidate in candidates:
            source_locale = detect_source_locale(candidate.source_text)
            if source_locale == locale:
                counts["same_locale_skipped_count"] += 1
                continue
            candidate_hash = source_text_hash(candidate.source_text)
            cached_row = exact_cache.get(
                (candidate_hash, candidate.text_kind, source_locale, locale)
            )
            cached = (
                cached_row
                if cached_row is not None and is_usable_localized_text_result(cached_row)
                else None
            )
            if cached_row is not None and cached is None:
                counts["invalid_cache_entry_count"] += 1
            if cached is not None and not force:
                if cached.source_text_hash != candidate_hash:
                    if dry_run:
                        counts["dry_run_missing_count"] += 1
                        counts["dry_run_legacy_hash_migration_count"] += 1
                        _record_affected(
                            candidate,
                            missing_by_text_kind,
                            affected_seen,
                            affected_property_ids,
                        )
                    else:
                        cache.upsert(
                            source_text=candidate.source_text,
                            translated_text=cached.translated_text,
                            text_kind=candidate.text_kind,
                            source_locale=source_locale,
                            target_locale=locale,
                            provider_name=cached.provider_name,
                            provider_model=cached.provider_model,
                            confidence=cached.confidence,
                            status=cached.status,
                        )
                        counts["legacy_hash_migration_count"] += 1
                    continue
                counts["cache_hit_count"] += 1
                continue
            if cached is not None and force:
                counts["force_refresh_count"] += 1
            if not force:
                reusable = reusable_cache.get(
                    (candidate_hash, source_locale, locale)
                )
                if reusable is not None:
                    if dry_run:
                        counts["dry_run_missing_count"] += 1
                        counts["dry_run_reusable_count"] += 1
                        _record_affected(
                            candidate,
                            missing_by_text_kind,
                            affected_seen,
                            affected_property_ids,
                        )
                        continue
                    cache.upsert(
                        source_text=candidate.source_text,
                        translated_text=reusable.translated_text,
                        text_kind=candidate.text_kind,
                        source_locale=source_locale,
                        target_locale=locale,
                        provider_name=reusable.provider_name,
                        provider_model=reusable.provider_model,
                        confidence=reusable.confidence,
                        status=reusable.status,
                    )
                    counts["cross_kind_cache_reuse_count"] += 1
                    continue
            if dry_run:
                counts["dry_run_missing_count"] += 1
                provider_group_seen.add((candidate.source_text, source_locale, locale))
                _record_affected(
                    candidate,
                    missing_by_text_kind,
                    affected_seen,
                    affected_property_ids,
                )
                continue
            provider_group_seen.add((candidate.source_text, source_locale, locale))
            pending.append((candidate, source_locale))
        if dry_run or not pending:
            continue
        if provider is None:
            for candidate, _source_locale in pending:
                counts["error_count"] += 1
                _record_affected(
                    candidate,
                    missing_by_text_kind,
                    affected_seen,
                    affected_property_ids,
                )
                _append_error(
                    errors,
                    candidate=candidate,
                    target_locale=locale,
                    error="localization provider is not configured",
                )
            continue
        if resolved_batch_size > 1:
            grouped_pending = _group_pending_by_source_text(pending)
            chunks = list(_chunks(grouped_pending, resolved_batch_size))

            def translate_chunk(chunk):
                decisions = []
                batch_error: Exception | None = None
                try:
                    decisions = provider.translate_many(
                        [
                            _translation_request(group_candidates[0], source_locale, locale)
                            for group_candidates, source_locale in chunk
                        ]
                    )
                except Exception as exc:  # noqa: BLE001 - keep batch refresh resumable
                    batch_error = exc
                return chunk, decisions, batch_error

            if batch_concurrency > 1 and len(chunks) > 1:
                with ThreadPoolExecutor(max_workers=max(1, batch_concurrency)) as executor:
                    translated_chunks = list(executor.map(translate_chunk, chunks))
            else:
                translated_chunks = [translate_chunk(chunk) for chunk in chunks]

            for chunk, decisions, batch_error in translated_chunks:
                counts["batch_provider_call_count"] += 1
                counts["provider_request_count"] += len(chunk)
                if batch_error is not None:
                    counts["batch_error_candidate_count"] += len(chunk)
                by_index = {
                    _decision_index(decision): decision
                    for decision in decisions
                    if _decision_index(decision) is not None
                }
                for index, (group_candidates, source_locale) in enumerate(chunk):
                    decision = by_index.get(index)
                    translated_text = apply_localization_glossary(
                        str((decision or {}).get("translated_text") or ""),
                        locale,
                    )
                    status = str((decision or {}).get("status") or "success")
                    batch_result_usable = (
                        batch_error is None
                        and decision is not None
                        and is_usable_translation(
                            source_text=group_candidates[0].source_text,
                            translated_text=translated_text,
                            source_locale=source_locale,
                            target_locale=locale,
                            status=status,
                        )
                    )
                    used_single_fallback = not batch_result_usable
                    if used_single_fallback:
                        if batch_error is None:
                            counts["batch_invalid_result_count"] += len(group_candidates)
                        if not single_fallback:
                            for candidate in group_candidates:
                                counts["error_count"] += 1
                                counts["invalid_provider_result_count"] += 1
                                _record_affected(
                                    candidate,
                                    missing_by_text_kind,
                                    affected_seen,
                                    affected_property_ids,
                                )
                                _append_error(
                                    errors,
                                    candidate=candidate,
                                    target_locale=locale,
                                    error=(
                                        str(batch_error)
                                        if batch_error is not None
                                        else "batch localization result was missing or unusable"
                                    ),
                                )
                            continue
                        counts["single_fallback_request_count"] += 1
                        try:
                            decision = provider.translate(
                                _translation_request(
                                    group_candidates[0],
                                    source_locale,
                                    locale,
                                )
                            )
                            translated_text = apply_localization_glossary(
                                str(decision.get("translated_text") or ""),
                                locale,
                            )
                            status = str(decision.get("status") or "success")
                            if not is_usable_translation(
                                source_text=group_candidates[0].source_text,
                                translated_text=translated_text,
                                source_locale=source_locale,
                                target_locale=locale,
                                status=status,
                            ):
                                raise RuntimeError(
                                    "single-item localization fallback returned an "
                                    f"unusable cross-locale result (status={status})"
                                )
                        except Exception as exc:  # noqa: BLE001 - item-level retry boundary
                            for candidate in group_candidates:
                                counts["error_count"] += 1
                                counts["invalid_provider_result_count"] += 1
                                _record_affected(
                                    candidate,
                                    missing_by_text_kind,
                                    affected_seen,
                                    affected_property_ids,
                                )
                                _append_error(
                                    errors,
                                    candidate=candidate,
                                    target_locale=locale,
                                    error=(
                                        f"{batch_error}; single fallback: {exc}"
                                        if batch_error is not None
                                        else str(exc)
                                    ),
                                )
                            continue
                    for candidate in group_candidates:
                        cache.upsert(
                            source_text=candidate.source_text,
                            translated_text=translated_text,
                            text_kind=candidate.text_kind,
                            source_locale=source_locale,
                            target_locale=locale,
                            provider_name=provider.provider_name,
                            provider_model=str(
                                decision.get("provider_model") or provider.provider_model
                            ),
                            confidence=_optional_float(decision.get("confidence")),
                            status=status,
                        )
                        counts["translated_count"] += 1
                        if used_single_fallback:
                            counts["single_fallback_translated_count"] += 1
                        else:
                            counts["batch_translated_count"] += 1
            continue
        for candidate, source_locale in pending:
            try:
                localize_text(
                    candidate.source_text,
                    text_kind=candidate.text_kind,
                    target_locale=locale,
                    source_locale=source_locale,
                    cache=cache,
                    provider=provider,
                    allow_provider=True,
                )
                counts["translated_count"] += 1
            except Exception as exc:  # noqa: BLE001 - keep batch refresh resumable
                counts["error_count"] += 1
                _record_affected(
                    candidate,
                    missing_by_text_kind,
                    affected_seen,
                    affected_property_ids,
                )
                _append_error(
                    errors,
                    candidate=candidate,
                    target_locale=locale,
                    error=str(exc),
                )

    return {
        "mode": "localization_refresh",
        "locales": resolved_locales,
        "text_kinds": list(wanted_kinds),
        "text_candidate_count": len(candidates),
        "batch_size": resolved_batch_size,
        "batch_concurrency": max(1, int(batch_concurrency)),
        "single_fallback_enabled": bool(single_fallback),
        "counts": dict(counts),
        "missing_count_by_text_kind": dict(missing_by_text_kind),
        "affected_property_count": len(affected_seen),
        "affected_property_ids": affected_property_ids[:100],
        "affected_property_ids_truncated": len(affected_property_ids) > 100,
        "provider_request_candidate_count": len(provider_group_seen),
        "error_count": counts["error_count"],
        "errors": errors,
        "dry_run": dry_run,
    }


def text_candidates_from_packets(
    packets: Iterable,
    *,
    text_kinds: Sequence[str] | None = None,
) -> Iterable[LocalizationTextCandidate]:
    wanted = set(text_kinds or DEFAULT_LOCALIZATION_TEXT_KINDS)
    seen: set[tuple[str, str]] = set()
    for packet in packets:
        property_id = str(packet.entity.property_id)
        text_items = [
            ("reason_to_recommend", packet.conclusion.reason_to_recommend),
            ("next_action", packet.conclusion.next_action),
            ("primary_metric.display_text", primary_metric_display_source_text(packet)),
        ]
        text_items.extend(
            ("evidence.field_value", evidence.field_value) for evidence in packet.evidence
        )
        text_items.extend(
            ("inference.inferred_value", inference.inferred_value) for inference in packet.inference
        )
        text_items.extend(
            ("inference.inference_basis", inference.inference_basis)
            for inference in packet.inference
        )
        text_items.extend(
            ("inference.inference_chain", inference.inference_chain)
            for inference in packet.inference
        )
        text_items.extend(("review.reason", review.reason) for review in packet.review_queue)
        text_items.extend(
            ("review.next_action", review.next_action) for review in packet.review_queue
        )
        for text_kind, source_text in text_items:
            if text_kind not in wanted:
                continue
            value = str(source_text or "").strip()
            if not value:
                continue
            key = (text_kind, source_text_hash(value))
            if key in seen:
                continue
            seen.add(key)
            yield LocalizationTextCandidate(
                property_id=property_id,
                text_kind=text_kind,
                source_text=value,
            )


def _resolved_locales(locales: Sequence[str] | None) -> list[str]:
    values = locales or supported_locales()
    resolved: list[str] = []
    for locale in values:
        value = resolve_locale(locale)
        if value not in resolved:
            resolved.append(value)
    return resolved


def _cache_indexes(
    results: Sequence[LocalizedTextResult],
) -> tuple[
    dict[tuple[str, str, str, str], LocalizedTextResult],
    dict[tuple[str, str, str], LocalizedTextResult],
]:
    exact: dict[tuple[str, str, str, str], LocalizedTextResult] = {}
    reusable: dict[tuple[str, str, str], LocalizedTextResult] = {}
    for result in results:
        normalized_hash = source_text_hash(result.source_text)
        source_locales = {result.source_locale}
        detected_source_locale = detect_source_locale(result.source_text)
        # Runtime lookup accepts a legacy "mixed" row for a now-dominant en/zh
        # source, but it does not accept a stale en/zh row for a genuinely mixed
        # source. Keep the dry-run index aligned with that one-way compatibility.
        if result.source_locale == "mixed" and detected_source_locale in {"en", "zh"}:
            source_locales.add(detected_source_locale)
        for source_locale in source_locales:
            exact_key = (
                normalized_hash,
                result.text_kind,
                source_locale,
                result.target_locale,
            )
            current = exact.get(exact_key)
            if current is None or (
                not is_usable_localized_text_result(current)
                and is_usable_localized_text_result(result)
            ):
                exact[exact_key] = result
            if is_usable_localized_text_result(result):
                reusable.setdefault(
                    (normalized_hash, source_locale, result.target_locale),
                    result,
                )
    return exact, reusable


def _resolved_batch_size(batch_size: int | None) -> int:
    if batch_size is not None:
        return max(1, int(batch_size))
    raw_value = os.getenv("ISITE2_LOCALIZATION_BATCH_SIZE", "20")
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 20


def _translation_request(
    candidate: LocalizationTextCandidate,
    source_locale: str,
    target_locale: str,
) -> dict:
    request = {
        "source_text": candidate.source_text,
        "text_kind": candidate.text_kind,
        "source_locale": source_locale,
        "target_locale": target_locale,
        "property_id": candidate.property_id,
        "requirements": [
            "Preserve numbers, units, years, URLs, source names, and original property names.",
            "Do not add facts or rewrite objective evidence.",
            "Keep the translation concise and auditable.",
        ],
    }
    if target_locale == "zh":
        request["translatable_latin_tokens"] = translatable_latin_tokens(
            candidate.source_text
        )
    return request


def _record_affected(
    candidate: LocalizationTextCandidate,
    missing_by_text_kind: Counter[str],
    affected_seen: set[str],
    affected_property_ids: list[str],
) -> None:
    missing_by_text_kind[candidate.text_kind] += 1
    if candidate.property_id not in affected_seen:
        affected_seen.add(candidate.property_id)
        affected_property_ids.append(candidate.property_id)


def _append_error(
    errors: list[dict[str, str]],
    *,
    candidate: LocalizationTextCandidate,
    target_locale: str,
    error: str,
) -> None:
    if len(errors) >= 25:
        return
    errors.append(
        {
            "property_id": candidate.property_id,
            "text_kind": candidate.text_kind,
            "target_locale": target_locale,
            "error": error,
        }
    )


def _chunks(
    values: Sequence,
    size: int,
) -> Iterable[Sequence]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _group_pending_by_source_text(
    pending: Sequence[tuple[LocalizationTextCandidate, str]],
) -> list[tuple[list[LocalizationTextCandidate], str]]:
    grouped: dict[tuple[str, str], list[LocalizationTextCandidate]] = {}
    for candidate, source_locale in pending:
        key = (source_text_hash(candidate.source_text), source_locale)
        grouped.setdefault(key, []).append(candidate)
    return [
        (candidates, source_locale)
        for (_source_hash, source_locale), candidates in grouped.items()
    ]


def _decision_index(decision: dict) -> int | None:
    try:
        return int(decision.get("index"))
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
