import hashlib
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, text

from isite2.growth.derived_refresh_trigger import _refresh_localization_after_derived
from isite2.growth.localization_refresh import (
    DEFAULT_LOCALIZATION_TEXT_KINDS,
    LocalizationTextCandidate,
    _translation_request,
    refresh_localization_cache_for_packets,
)
from isite2.localization import (
    _TRANSLATION_SYSTEM_PROMPT,
    _TRANSLATION_BATCH_OUTPUT_SCHEMA,
    _TRANSLATION_OUTPUT_SCHEMA,
    DatabaseLocalizationCache,
    apply_localization_glossary,
    detect_source_locale,
    enum_label,
    localization_payload,
    localize_packet,
    localize_text,
    is_usable_translation,
    resolve_locale,
    scene_label,
    source_text_hash,
    t,
)


def test_localizer_labels_and_fallbacks() -> None:
    assert resolve_locale("zh-CN") == "zh"
    assert resolve_locale("fr") == "en"
    assert scene_label("airport_terminal", "en") == "Airport"
    assert scene_label("airport_terminal", "zh") == "机场"
    assert enum_label("Direct Recommend", "zh") == "直接推荐"
    assert enum_label("Context", "en") == "Context"
    assert enum_label("Context", "zh") == "上下文证据"
    assert t("ui.opportunity_globe", "zh") == "机会地图"
    assert scene_label("custom_scene", "zh") == "custom scene"


def test_default_localization_refresh_covers_inference_texts() -> None:
    assert "inference.inferred_value" in DEFAULT_LOCALIZATION_TEXT_KINDS
    assert "inference.inference_basis" in DEFAULT_LOCALIZATION_TEXT_KINDS
    assert "inference.inference_chain" in DEFAULT_LOCALIZATION_TEXT_KINDS


def test_post_derived_localization_scans_all_texts_referenced_by_run(monkeypatch) -> None:
    packets = [
        SimpleNamespace(entity=SimpleNamespace(property_id="p1")),
        SimpleNamespace(entity=SimpleNamespace(property_id="p2")),
    ]

    class FakeRepository:
        engine = object()

        def list_properties(self, filters):
            assert filters == {"scan_run_id": str(scan_run_id)}
            return packets

    captured = {}

    def fake_refresh(values, engine, **_kwargs):
        captured["packets"] = list(values)
        captured["engine"] = engine
        return {"counts": {"cache_hit_count": 1}}

    scan_run_id = uuid4()
    monkeypatch.setenv("ISITE2_ENABLE_LOCALIZATION_REFRESH_AFTER_DERIVED", "1")
    monkeypatch.setattr(
        "isite2.growth.derived_refresh_trigger.refresh_localization_cache_for_packets",
        fake_refresh,
    )

    summary = _refresh_localization_after_derived(
        FakeRepository(),
        scan_run_id,
        property_ids=["p1"],
    )

    assert captured["packets"] == packets
    assert summary["refresh_scope"] == "scan_run_referenced_text_hashes"
    assert summary["requested_changed_property_count"] == 1


def test_localization_payload_shape() -> None:
    payload = localization_payload("zh")

    assert payload["locale"] == "zh"
    assert payload["default_locale"] == "en"
    assert payload["supported_locales"] == ["en", "zh"]
    assert payload["labels"]["excel"]["sheets"]["main"] == "主表"


def test_codex_oauth_translation_schema_requires_all_properties() -> None:
    assert set(_TRANSLATION_OUTPUT_SCHEMA["required"]) == set(
        _TRANSLATION_OUTPUT_SCHEMA["properties"]
    )
    batch_item_schema = _TRANSLATION_BATCH_OUTPUT_SCHEMA["properties"]["translations"]["items"]
    assert set(batch_item_schema["required"]) == set(batch_item_schema["properties"])


def test_translation_prompt_requires_mixed_language_clauses_to_be_translated() -> None:
    assert "mixed-language source is not already in the target language" in (
        _TRANSLATION_SYSTEM_PROMPT
    )
    assert "never use this shortcut when source_locale is mixed" in (
        _TRANSLATION_SYSTEM_PROMPT
    )
    assert "keys/rooms" in _TRANSLATION_SYSTEM_PROMPT
    assert "Standard SI unit abbreviations" in _TRANSLATION_SYSTEM_PROMPT


def test_batch_translation_request_names_tokens_that_must_be_localized() -> None:
    request = _translation_request(
        LocalizationTextCandidate(
            property_id="p1",
            text_kind="next_action",
            source_text="补采房量（keys/rooms）和 12,000 sqm 的会议空间。",
        ),
        "mixed",
        "zh",
    )

    assert request["translatable_latin_tokens"] == ["keys", "rooms"]


def test_localized_text_cache_upsert_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)

    missing = localize_text(
        "补查机场年报 2024 旅客吞吐量",
        text_kind="next_action",
        target_locale="en",
        cache=cache,
        allow_provider=False,
    )
    assert missing.status == "cache_miss"
    assert missing.translated_text == "Localization pending"

    first = cache.upsert(
        source_text="补查机场年报 2024 旅客吞吐量",
        translated_text="Check the airport 2024 annual report passenger throughput.",
        text_kind="next_action",
        target_locale="en",
        source_locale="zh",
        provider_name="fake",
        provider_model="fake-model",
        confidence=0.9,
    )
    second = cache.upsert(
        source_text="补查机场年报 2024 旅客吞吐量",
        translated_text="Check airport 2024 annual report passenger throughput.",
        text_kind="next_action",
        target_locale="en",
        source_locale="zh",
        provider_name="fake",
        provider_model="fake-model",
        confidence=0.91,
    )
    cached = cache.lookup(
        source_text="补查机场年报 2024 旅客吞吐量",
        text_kind="next_action",
        target_locale="en",
        source_locale="zh",
    )

    assert first.source_text_hash == second.source_text_hash
    assert cached is not None
    assert cached.translated_text == "Check airport 2024 annual report passenger throughput."


def test_source_text_hash_normalizes_unicode_and_whitespace() -> None:
    assert source_text_hash("Ｔotal   rooms:\n740") == source_text_hash("Total rooms: 740")


def test_source_locale_detection_uses_dominant_language() -> None:
    assert (
        detect_source_locale(
            "优先复核航站楼平面与分区面积，随后进入 pRRU 方案比选和 Review Queue。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "The 北京项目 airport terminal has measurable annual passenger demand."
        )
        == "en"
    )
    assert detect_source_locale("推荐理由包含 English metric details") == "mixed"
    assert (
        detect_source_locale(
            "补充 GR Ljubljana Exhibition and Convention Centre 的第二独立来源，"
            "交叉核验当前主指标数值与年份。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "补采 office_nla、building_grade 与 cbd_role 后重新判定。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "补充核验该场馆的event_days、典型赛事频次与峰值客流，"
            "再维持hRRU方案。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "已有明确、可核验的面积型硬指标，项目为 12,000 sqm，"
            "适合按 GLA 驱动并采用 pRRU。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "酒店场景的硬量化主指标为 148 keys，适合推进 pRRU 方案。"
        )
        == "mixed"
    )
    assert (
        detect_source_locale(
            "补查 Palais des Congrès de Marrakech 的官方 factsheet。"
        )
        == "mixed"
    )
    assert (
        detect_source_locale(
            "补充 ibis Malabo 的第二独立来源，交叉核验当前主指标数值与年份。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "补查 Estación Río de los Remedios (Metro CDMX) 的官方日客流、换乘量或线路数量。"
        )
        == "zh"
    )
    assert (
        detect_source_locale(
            "Gambir railway station 具备 3 条轨道线路成员关系，推荐 pRRU。"
        )
        == "zh"
    )


def test_zh_localization_glossary_translates_fixed_display_terms() -> None:
    assert apply_localization_glossary(
        "scene enrollment=6000; 2,000 sqft; `footfall`; `keys`; null",
        "zh",
    ) == "场景 在校生数=6000; 2,000 平方英尺; `客流量`; `客房数`; 暂无"


def test_locale_neutral_metric_acronym_does_not_require_translation() -> None:
    assert is_usable_translation(
        source_text="GLA: 49,000 m2",
        translated_text="GLA: 49,000 m2",
        source_locale="en",
        target_locale="zh",
        status="ok",
    )
    assert not is_usable_translation(
        source_text="GLA: MediaWiki infobox floor_area: 27000 m2",
        translated_text="GLA: MediaWiki infobox floor_area: 27000 m2",
        source_locale="en",
        target_locale="zh",
        status="ok",
    )


def test_dominant_locale_lookup_reuses_legacy_mixed_cache_key() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source = "优先复核航站楼平面与分区面积，随后进入 pRRU 方案比选。"
    cache.upsert(
        source_text=source,
        translated_text=(
            "Verify the terminal layout and zoned floor area, then compare pRRU options."
        ),
        text_kind="next_action",
        source_locale="mixed",
        target_locale="en",
        status="success",
    )

    cached = cache.lookup(
        source_text=source,
        text_kind="next_action",
        source_locale="zh",
        target_locale="en",
    )

    assert cached is not None
    assert cached.translated_text.startswith("Verify the terminal layout")


def test_refresh_dry_run_does_not_treat_stale_zh_row_as_mixed_to_en_hit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source = (
        "旗舰商业综合体，mall total area: around 50,000 square meters，"
        "周末与节假日峰值明显。"
    )
    cache.upsert(
        source_text=source,
        translated_text="Flagship commercial complex with a total area of 50,000 sqm.",
        text_kind="reason_to_recommend",
        source_locale="zh",
        target_locale="en",
        status="success",
    )
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="property-1",
            scene_type="large_retail",
        ),
        conclusion=SimpleNamespace(
            reason_to_recommend=source,
            next_action="补查第二独立来源。",
        ),
        evidence=[],
        inference=[],
        review_queue=[],
        scene=SimpleNamespace(
            area_metric_name="gla",
            proxy_level="P1",
        ),
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["en"],
        text_kinds=["reason_to_recommend"],
        dry_run=True,
    )

    assert detect_source_locale(source) == "mixed"
    assert summary["counts"]["dry_run_missing_count"] == 1
    assert summary["provider_request_candidate_count"] == 1


def test_localized_text_cache_reads_legacy_raw_hash_rows() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source = "Total   rooms:\n740"
    legacy_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO localized_text_cache (
                  id, source_text_hash, text_kind, source_locale, target_locale,
                  schema_version, source_text, translated_text, status
                ) VALUES (
                  'legacy-row', :source_text_hash, 'primary_metric.display_text',
                  'en', 'zh', 'localization.v1', :source_text, :translated_text, 'success'
                )
                """
            ),
            {
                "source_text_hash": legacy_hash,
                "source_text": source,
                "translated_text": "客房总数：740",
            },
        )

    cached = cache.lookup(
        source_text=source,
        text_kind="primary_metric.display_text",
        source_locale="en",
        target_locale="zh",
    )

    assert cached is not None
    assert cached.translated_text == "客房总数：740"
    assert cached.source_text_hash == legacy_hash


def test_unusable_cross_locale_cache_rows_are_treated_as_missing() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source = "Check the airport annual report."
    cache.upsert(
        source_text=source,
        translated_text=source,
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
        provider_name="noop",
        provider_model="noop",
        status="skipped_no_provider",
    )

    assert (
        cache.lookup(
            source_text=source,
            text_kind="next_action",
            source_locale="en",
            target_locale="zh",
        )
        is None
    )
    raw = cache.lookup(
        source_text=source,
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
        usable_only=False,
    )
    cache.upsert(
        source_text=source,
        translated_text=f"{source}。",
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
        provider_name="bad-provider",
        provider_model="bad-model",
        status="ok",
    )
    assert (
        cache.lookup(
            source_text=source,
            text_kind="next_action",
            source_locale="en",
            target_locale="zh",
        )
        is None
    )
    localized = localize_text(
        source,
        text_kind="next_action",
        target_locale="zh",
        cache=cache,
        allow_provider=False,
    )

    assert raw is not None
    assert raw.status == "skipped_no_provider"
    assert localized.status == "cache_miss"
    assert localized.translated_text == "本地化待刷新"


def test_localize_text_same_locale_and_cache_miss_placeholder_do_not_expose_raw() -> None:
    same_locale = localize_text(
        "补查机场年报 2024 旅客吞吐量",
        text_kind="next_action",
        target_locale="zh",
        allow_provider=False,
    )
    assert same_locale.status == "same_locale"
    assert same_locale.translated_text == "补查机场年报 2024 旅客吞吐量"

    missing_english = localize_text(
        "补查机场年报 2024 旅客吞吐量",
        text_kind="next_action",
        target_locale="en",
        allow_provider=False,
    )
    assert missing_english.status == "cache_miss"
    assert missing_english.source_locale == "zh"
    assert missing_english.translated_text == "Localization pending"

    missing_chinese = localize_text(
        "Travel Weekly hotel profile lists 740 rooms.",
        text_kind="primary_metric.display_text",
        target_locale="zh",
        allow_provider=False,
    )
    assert missing_chinese.status == "cache_miss"
    assert missing_chinese.source_locale == "en"
    assert missing_chinese.translated_text == "本地化待刷新"

    mixed_chinese = localize_text(
        "推荐理由包含 English metric details",
        text_kind="reason_to_recommend",
        target_locale="zh",
        allow_provider=False,
    )
    assert mixed_chinese.status == "cache_miss"
    assert mixed_chinese.source_locale == "mixed"
    assert mixed_chinese.translated_text == "本地化待刷新"


def test_localize_packet_primary_metric_uses_database_cache() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="p1",
            property_name="Hotel Example",
            scene_type="luxury_hotel_mice",
        ),
        scene=SimpleNamespace(area_metric_name="keys", proxy_level="P1 Strong Proxy"),
        build_status=SimpleNamespace(
            indoor_system_presence="Unknown",
            indoor_system_type="Unknown",
            indoor_rat="Unknown",
            build_evidence_status="No Public Evidence",
        ),
        conclusion=SimpleNamespace(
            evidence_status="Supported",
            value_class="City Core",
            action_class="Survey First",
            recommended_solution="pRRU",
            reason_to_recommend="Travel profile supports hotel demand.",
            next_action="Check a second independent source.",
        ),
        evidence=[
            SimpleNamespace(
                field_group="keys",
                indicator_name=None,
                field_value="Travel Weekly hotel profile lists 740 rooms.",
                evidence_type="Direct",
                source_tier="Tier 3",
                cross_check_status="Single Source",
            )
        ],
        inference=[],
        review_queue=[],
    )

    missing = localize_packet(packet, locale="zh", cache=cache, allow_provider=False)
    assert missing["primary_metric"]["display_text"] == "本地化待刷新"
    assert missing["primary_metric"]["localization_status"] == "cache_miss"

    cache.upsert(
        source_text="Rooms / keys: Travel Weekly hotel profile lists 740 rooms.",
        translated_text="房间数：740 间",
        text_kind="primary_metric.display_text",
        target_locale="zh",
        source_locale="en",
        provider_name="fake",
        provider_model="fake-model",
    )
    localized = localize_packet(packet, locale="zh", cache=cache, allow_provider=False)
    assert localized["primary_metric"]["display_text"] == "房间数：740 间"
    assert localized["primary_metric"]["localization_status"] == "success"


def test_localize_packet_marks_context_only_designated_lead_metric_missing() -> None:
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="p-designated",
            property_name="Designated Hotel",
            scene_type="luxury_hotel_mice",
        ),
        scene=SimpleNamespace(area_metric_name="keys", proxy_level="P3 Weak Proxy"),
        build_status=SimpleNamespace(
            indoor_system_presence="Unknown",
            indoor_system_type="Unknown",
            indoor_rat="Unknown",
            build_evidence_status="No Public Evidence",
        ),
        conclusion=SimpleNamespace(
            evidence_status="Insufficient",
            value_class="Observation",
            action_class="Survey First",
            recommended_solution="Unknown",
            reason_to_recommend="主指标量化证据不足。",
            next_action="补查官方客房数。",
        ),
        evidence=[
            SimpleNamespace(
                field_group="property_identity",
                indicator_name="property_identity",
                field_value="Designated lead identity confirmed; primary metric missing.",
                evidence_type="Context",
                source_tier="Tier 3",
                cross_check_status="Single Source",
            )
        ],
        inference=[],
        review_queue=[],
    )

    chinese = localize_packet(packet, locale="zh", allow_provider=False)
    english = localize_packet(packet, locale="en", allow_provider=False)

    assert chinese["primary_metric"]["display_text"] == "主指标缺失"
    assert english["primary_metric"]["display_text"] == "Missing primary metric"
    assert chinese["primary_metric"]["localization_status"] == "missing"


def test_localization_refresh_defaults_include_english_missing_texts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    packet = SimpleNamespace(
        entity=SimpleNamespace(property_id="p1"),
        conclusion=SimpleNamespace(
            reason_to_recommend="推荐理由中文",
            next_action="补查下一步动作",
        ),
        evidence=[],
        inference=[],
        review_queue=[],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        provider_mode="noop",
        dry_run=True,
    )

    assert "en" in summary["locales"]
    assert summary["counts"]["dry_run_missing_count"] == 2
    assert summary["counts"]["same_locale_skipped_count"] == 2
    assert summary["missing_count_by_text_kind"] == {
        "reason_to_recommend": 1,
        "next_action": 1,
    }
    assert summary["affected_property_count"] == 1
    assert summary["affected_property_ids"] == ["p1"]


def test_localization_refresh_batches_missing_texts_into_cache() -> None:
    class FakeBatchProvider:
        provider_name = "fake_batch"
        provider_model = "fake-model"

        def __init__(self) -> None:
            self.batches = []

        def translate(self, request):
            raise AssertionError("single translate should not be used for batch refresh")

        def translate_many(self, requests):
            self.batches.append(list(requests))
            return [
                {
                    "index": index,
                        "translated_text": f"翻译：{request['source_text']}",
                    "confidence": 0.9,
                    "status": "success",
                    "provider_model": self.provider_model,
                }
                for index, request in enumerate(requests)
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    provider = FakeBatchProvider()
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="p1",
            scene_type="luxury_hotel_mice",
        ),
        conclusion=SimpleNamespace(
            reason_to_recommend="Hotel profile supports business demand.",
            next_action="Check a second independent source.",
        ),
        evidence=[
            SimpleNamespace(
                field_group="keys",
                indicator_name=None,
                field_value="Travel Weekly hotel profile lists 740 rooms.",
            )
        ],
        inference=[],
        review_queue=[],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=provider,
        batch_size=10,
    )
    cache = DatabaseLocalizationCache(engine)
    cached = cache.lookup(
        source_text="Hotel profile supports business demand.",
        text_kind="reason_to_recommend",
        source_locale="en",
        target_locale="zh",
    )

    assert summary["counts"]["batch_translated_count"] == 4
    assert summary["counts"]["translated_count"] == 4
    assert summary["text_candidate_count"] == 4
    assert len(provider.batches) == 1
    assert cached is not None
    assert cached.translated_text == "翻译：Hotel profile supports business demand."

    forced = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=provider,
        batch_size=10,
        force=True,
    )
    assert forced["counts"]["force_refresh_count"] == 4
    assert forced["counts"]["batch_translated_count"] == 4


def test_localization_refresh_repairs_invalid_cache_entries() -> None:
    class FakeBatchProvider:
        provider_name = "fake_batch"
        provider_model = "fake-model"

        def translate(self, request):
            raise AssertionError("single translate should not be used")

        def translate_many(self, requests):
            return [
                {
                    "index": index,
                    "translated_text": "补查机场年报。",
                    "confidence": 0.9,
                    "status": "success",
                    "provider_model": self.provider_model,
                }
                for index, _request in enumerate(requests)
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source = "Check the airport annual report."
    cache.upsert(
        source_text=source,
        translated_text=source,
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
        status="skipped_no_provider",
    )
    packet = SimpleNamespace(
        entity=SimpleNamespace(property_id="p1"),
        conclusion=SimpleNamespace(reason_to_recommend="", next_action=source),
        evidence=[],
        inference=[],
        review_queue=[],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=FakeBatchProvider(),
        batch_size=10,
    )
    repaired = cache.lookup(
        source_text=source,
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
    )

    assert summary["counts"]["invalid_cache_entry_count"] == 1
    assert summary["counts"]["translated_count"] == 1
    assert repaired is not None
    assert repaired.translated_text == "补查机场年报。"


def test_localization_refresh_does_not_persist_unusable_provider_results() -> None:
    class InvalidProvider:
        provider_name = "invalid"
        provider_model = "invalid-model"

        def translate(self, request):
            return {
                "translated_text": request["source_text"],
                "confidence": 0.0,
                "status": "skipped_no_provider",
                "provider_model": self.provider_model,
            }

        def translate_many(self, requests):
            return [
                {
                    "index": index,
                    "translated_text": request["source_text"],
                    "confidence": 0.0,
                    "status": "skipped_no_provider",
                    "provider_model": self.provider_model,
                }
                for index, request in enumerate(requests)
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    packet = SimpleNamespace(
        entity=SimpleNamespace(property_id="p1"),
        conclusion=SimpleNamespace(
            reason_to_recommend="Business demand is supported.",
            next_action="",
        ),
        evidence=[],
        inference=[],
        review_queue=[],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=InvalidProvider(),
        batch_size=10,
    )
    cache = DatabaseLocalizationCache(engine)

    assert summary["counts"]["invalid_provider_result_count"] == 1
    assert summary["counts"]["single_fallback_request_count"] == 1
    assert summary["error_count"] == 1
    assert (
        cache.lookup(
            source_text="Business demand is supported.",
            text_kind="reason_to_recommend",
            source_locale="en",
            target_locale="zh",
            usable_only=False,
        )
        is None
    )


def test_localization_refresh_retries_invalid_batch_item_singly() -> None:
    class FallbackProvider:
        provider_name = "fallback"
        provider_model = "fallback-model"

        def translate_many(self, requests):
            return [
                {
                    "index": index,
                    "translated_text": request["source_text"],
                    "confidence": 0.5,
                    "status": "ok",
                    "provider_model": self.provider_model,
                }
                for index, request in enumerate(requests)
            ]

        def translate(self, request):
            return {
                "translated_text": "补查机场年报。",
                "confidence": 0.95,
                "status": "ok",
                "provider_model": self.provider_model,
            }

    engine = create_engine("sqlite+pysqlite:///:memory:")
    source = "Check the airport annual report."
    packet = SimpleNamespace(
        entity=SimpleNamespace(property_id="p1"),
        conclusion=SimpleNamespace(reason_to_recommend="", next_action=source),
        evidence=[],
        inference=[],
        review_queue=[],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=FallbackProvider(),
        batch_size=5,
    )
    cached = DatabaseLocalizationCache(engine).lookup(
        source_text=source,
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
    )

    assert summary["error_count"] == 0
    assert summary["counts"]["batch_invalid_result_count"] == 1
    assert summary["counts"]["single_fallback_request_count"] == 1
    assert summary["counts"]["single_fallback_translated_count"] == 1
    assert cached is not None
    assert cached.translated_text == "补查机场年报。"


def test_localization_refresh_deduplicates_same_source_text_across_kinds() -> None:
    class FakeBatchProvider:
        provider_name = "fake_batch"
        provider_model = "fake-model"

        def __init__(self) -> None:
            self.batches = []

        def translate(self, request):
            raise AssertionError("single translate should not be used for batch refresh")

        def translate_many(self, requests):
            self.batches.append(list(requests))
            return [
                {
                    "index": index,
                        "translated_text": f"翻译：{request['source_text']}",
                    "confidence": 0.9,
                    "status": "success",
                    "provider_model": self.provider_model,
                }
                for index, request in enumerate(requests)
            ]

    engine = create_engine("sqlite+pysqlite:///:memory:")
    provider = FakeBatchProvider()
    repeated_text = "Check a second independent source."
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="p1",
            scene_type="luxury_hotel_mice",
        ),
        conclusion=SimpleNamespace(
            reason_to_recommend=repeated_text,
            next_action=repeated_text,
        ),
        evidence=[],
        inference=[],
        review_queue=[
            SimpleNamespace(
                reason=repeated_text,
                next_action=repeated_text,
                status="open",
            )
        ],
    )

    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=provider,
        batch_size=10,
    )
    cache = DatabaseLocalizationCache(engine)

    assert summary["counts"]["provider_request_count"] == 1
    assert summary["counts"]["batch_translated_count"] == 4
    assert summary["text_candidate_count"] == 4
    assert len(provider.batches) == 1
    assert len(provider.batches[0]) == 1
    for text_kind in (
        "reason_to_recommend",
        "next_action",
        "review.reason",
        "review.next_action",
    ):
        cached = cache.lookup(
            source_text=repeated_text,
            text_kind=text_kind,
            source_locale="en",
            target_locale="zh",
        )
        assert cached is not None
        assert cached.translated_text == f"翻译：{repeated_text}"


def test_localization_refresh_runs_independent_batches_concurrently() -> None:
    class ConcurrentProvider:
        provider_name = "concurrent"
        provider_model = "concurrent-model"

        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def translate(self, request):
            raise AssertionError("single translate should not be used")

        def translate_many(self, requests):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return [
                {
                    "index": index,
                    "translated_text": f"翻译：{request['source_text']}",
                    "confidence": 0.9,
                    "status": "success",
                    "provider_model": self.provider_model,
                }
                for index, request in enumerate(requests)
            ]

    provider = ConcurrentProvider()
    packets = [
        SimpleNamespace(
            entity=SimpleNamespace(property_id=f"p{index}", scene_type="office_government"),
            conclusion=SimpleNamespace(
                reason_to_recommend="",
                next_action=f"Check official source number {index}.",
            ),
            evidence=[],
            inference=[],
            review_queue=[],
        )
        for index in range(4)
    ]

    summary = refresh_localization_cache_for_packets(
        packets,
        create_engine("sqlite+pysqlite:///:memory:"),
        locales=["zh"],
        text_kinds=["next_action"],
        provider=provider,
        batch_size=2,
        batch_concurrency=2,
    )

    assert provider.max_active == 2
    assert summary["batch_concurrency"] == 2
    assert summary["counts"]["translated_count"] == 4


def test_localization_refresh_can_defer_single_item_fallback() -> None:
    class InvalidBatchProvider:
        provider_name = "invalid_batch"
        provider_model = "invalid-model"

        def translate_many(self, requests):
            return []

        def translate(self, request):
            raise AssertionError("single fallback must be deferred")

    packet = SimpleNamespace(
        entity=SimpleNamespace(property_id="p1", scene_type="office_government"),
        conclusion=SimpleNamespace(
            reason_to_recommend="",
            next_action="Check the official annual report.",
        ),
        evidence=[],
        inference=[],
        review_queue=[],
    )
    summary = refresh_localization_cache_for_packets(
        [packet],
        create_engine("sqlite+pysqlite:///:memory:"),
        locales=["zh"],
        text_kinds=["next_action"],
        provider=InvalidBatchProvider(),
        batch_size=2,
        single_fallback=False,
    )

    assert summary["single_fallback_enabled"] is False
    assert summary["counts"].get("single_fallback_request_count", 0) == 0
    assert summary["error_count"] == 1


def test_localization_refresh_reuses_existing_translation_across_kinds() -> None:
    class FailingProvider:
        provider_name = "failing"
        provider_model = "failing-model"

        def translate(self, request):
            raise AssertionError("provider should not be called")

        def translate_many(self, requests):
            raise AssertionError("provider should not be called")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    cache = DatabaseLocalizationCache(engine)
    source_text = "Check a second independent source."
    cache.upsert(
        source_text=source_text,
        translated_text="补查第二独立来源。",
        text_kind="next_action",
        source_locale="en",
        target_locale="zh",
        provider_name="seed",
        provider_model="seed-model",
    )
    packet = SimpleNamespace(
        entity=SimpleNamespace(
            property_id="p1",
            scene_type="luxury_hotel_mice",
        ),
        conclusion=SimpleNamespace(
            reason_to_recommend=source_text,
            next_action=source_text,
        ),
        evidence=[],
        inference=[],
        review_queue=[],
    )

    dry_run = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=FailingProvider(),
        batch_size=10,
        dry_run=True,
    )
    summary = refresh_localization_cache_for_packets(
        [packet],
        engine,
        locales=["zh"],
        provider=FailingProvider(),
        batch_size=10,
    )
    reused = cache.lookup(
        source_text=source_text,
        text_kind="reason_to_recommend",
        source_locale="en",
        target_locale="zh",
    )

    assert dry_run["counts"]["dry_run_missing_count"] == 1
    assert dry_run["counts"]["dry_run_reusable_count"] == 1
    assert summary["counts"]["cache_hit_count"] == 1
    assert summary["counts"]["cross_kind_cache_reuse_count"] == 1
    assert reused is not None
    assert reused.translated_text == "补查第二独立来源。"
