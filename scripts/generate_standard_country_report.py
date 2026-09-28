from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from qa_standard_report_output_bundle import (
    audit_report_dir,
    audit_report_dirs,
    resolve_qa_lessons_path,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from isite2.domain.models import SitePacket
from isite2.localization import DatabaseLocalizationCache
from isite2.output.excel import (
    RecommendationDecision,
    _build_recommendation_index,
    write_excel_skeleton,
)
from isite2.output.ppt import (
    prepare_ppt_assets,
    scene_card_image_candidates,
    scene_card_scene_types,
    write_ppt_deck,
)
from isite2.repositories import get_default_repository
from isite2.rules.candidate_quality import filter_packets_for_surface


SUPPORTED_LOCALES = ("zh", "en")
REPORT_PAYLOAD_SCHEMA_VERSION = 1
REPORT_GENERATOR_VERSION = "artifact-aware-batch-v2"
ARTIFACT_MODES = ("all", "excel-only")


@dataclass
class CountryJob:
    country: str
    slug: str
    report_dir: Path
    packets: list[SitePacket]
    export_packets: list[SitePacket]
    recommendations: dict[str, dict[str, RecommendationDecision]]
    representatives: list[SitePacket]
    payload: dict[str, Any]
    content_hash: str
    reused_from: Path | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one or more standard iSite2 country report packages. The command "
            "freezes one shared payload per country, reuses unchanged audited outputs, "
            "runs a high-density smoke first, generates remaining countries in parallel, "
            "and performs one batch GPT audit."
        )
    )
    parser.add_argument("--country", action="append", required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--locale", action="append", choices=SUPPORTED_LOCALES)
    parser.add_argument("--include-blocked-quality", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument(
        "--artifact-mode",
        choices=ARTIFACT_MODES,
        default="all",
        help=(
            "Generate the full Excel/PPT package or audited Excel files only. "
            "The public country-download publisher uses excel-only."
        ),
    )
    parser.add_argument(
        "--recommendation-metric-gpt",
        choices=["auto", "0", "off", "codex-oauth", "gpt", "openai"],
        default="0",
        help=(
            "Defaults to 0 because the active packet already carries canonical metrics; "
            "ambiguous source evidence should be normalized before report generation."
        ),
    )
    parser.add_argument(
        "--gpt-audit-provider",
        choices=["codex-oauth", "off"],
        default="codex-oauth",
        help="One shared Codex OAuth audit is run for all newly generated countries.",
    )
    parser.add_argument("--audit-output", type=Path, default=None)
    args = parser.parse_args()

    metric_gpt = args.recommendation_metric_gpt
    os.environ["ISITE2_RECOMMENDATION_METRIC_GPT"] = (
        "0" if metric_gpt == "off" else metric_gpt
    )
    os.environ.setdefault("ISITE2_RECOMMENDATION_METRIC_GPT_PROVIDER", "codex-oauth")

    started = time.perf_counter()
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    locales = tuple(dict.fromkeys(args.locale or SUPPORTED_LOCALES))
    countries = list(dict.fromkeys(country.strip() for country in args.country if country.strip()))
    if not countries:
        raise SystemExit("at least one non-empty --country is required")
    args.output_root.mkdir(parents=True, exist_ok=True)

    repository = get_default_repository()
    localization_cache = _localization_cache(repository)
    jobs = [
        _build_country_job(
            country,
            output_root=args.output_root,
            timestamp=timestamp,
            locales=locales,
            repository=repository,
            include_blocked_quality=args.include_blocked_quality,
            artifact_mode=args.artifact_mode,
        )
        for country in countries
    ]
    _emit_event(
        "payloads_ready",
        started,
        countries=len(jobs),
        packets=sum(len(job.export_packets) for job in jobs),
    )
    if not args.force_regenerate:
        for job in jobs:
            job.reused_from = _find_reusable_report(
                args.output_root,
                job,
                locales,
                artifact_mode=args.artifact_mode,
                require_gpt_audit=args.gpt_audit_provider == "codex-oauth",
            )

    reused_jobs = [job for job in jobs if job.reused_from is not None]
    new_jobs = [job for job in jobs if job.reused_from is None]
    for job in reused_jobs:
        _reuse_country_artifacts(
            job,
            locales,
            timestamp,
            artifact_mode=args.artifact_mode,
        )
    _emit_event(
        "reuse_checked",
        started,
        reused=len(reused_jobs),
        regenerate=len(new_jobs),
    )

    image_assets: dict[str, Path] = {}
    if new_jobs and args.artifact_mode == "all":
        asset_started = time.perf_counter()
        representatives = [
            packet
            for job in new_jobs
            for packet in job.representatives
        ]
        image_assets = prepare_ppt_assets(
            representatives,
            max_workers=max(1, args.max_workers * 2),
        )
        attempted_image_urls = {
            str(packet.entity.hero_image.url)
            for packet in representatives
            if packet.entity.hero_image
        }
        fallback_candidates = [
            packet
            for job in new_jobs
            for packet in _missing_scene_image_candidates(
                job,
                image_assets,
                attempted_image_urls,
            )
        ]
        if fallback_candidates:
            fallback_started = time.perf_counter()
            image_assets.update(
                prepare_ppt_assets(
                    fallback_candidates,
                    max_workers=max(1, args.max_workers * 2),
                )
            )
            _emit_event(
                "ppt_asset_fallback_completed",
                started,
                stage_seconds=round(time.perf_counter() - fallback_started, 3),
                requested=len(
                    {
                        str(packet.entity.hero_image.url)
                        for packet in fallback_candidates
                        if packet.entity.hero_image
                    }
                ),
                resolved_total=len(image_assets),
            )
        missing_scene_images = {
            job.country: _missing_scene_images(job, image_assets)
            for job in new_jobs
        }
        missing_scene_images = {
            country: scenes
            for country, scenes in missing_scene_images.items()
            if scenes
        }
        if missing_scene_images:
            raise RuntimeError(
                "real property image preflight failed: "
                f"{json.dumps(missing_scene_images, ensure_ascii=False)}"
            )
        _emit_event(
            "ppt_assets_ready",
            started,
            stage_seconds=round(time.perf_counter() - asset_started, 3),
            requested=len(
                {
                    str(packet.entity.hero_image.url)
                    for packet in representatives
                    if packet.entity.hero_image
                }
            ),
            resolved=len(image_assets),
        )
    if new_jobs:
        ordered_jobs = sorted(
            new_jobs,
            key=lambda job: (
                -len({packet.entity.scene_type for packet in job.export_packets}),
                -len(job.export_packets),
                job.country.casefold(),
            ),
        )
        smoke_job = ordered_jobs[0]
        _generate_country_artifacts(
            smoke_job,
            locales,
            timestamp,
            localization_cache,
            image_assets,
            artifact_mode=args.artifact_mode,
        )
        _emit_event(
            "smoke_generated",
            started,
            country=smoke_job.country,
            scenes=len(
                {packet.entity.scene_type for packet in smoke_job.export_packets}
            ),
        )
        smoke_audit = audit_report_dir(
            smoke_job.report_dir,
            country=smoke_job.country,
            locales=locales,
            gpt_provider="off",
            artifact_mode=args.artifact_mode,
        )
        if smoke_audit["status"] != "pass":
            raise RuntimeError(
                f"high-density one-page smoke failed for {smoke_job.country}: "
                f"{json.dumps(smoke_audit, ensure_ascii=False)}"
            )
        _emit_event("smoke_passed", started, country=smoke_job.country)
        remaining_jobs = ordered_jobs[1:]
        if remaining_jobs:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, min(args.max_workers, len(remaining_jobs)))
            ) as executor:
                futures = {
                    executor.submit(
                        _generate_country_artifacts,
                        job,
                        locales,
                        timestamp,
                        localization_cache,
                        image_assets,
                        args.artifact_mode,
                    ): job
                    for job in remaining_jobs
                }
                for future in concurrent.futures.as_completed(futures):
                    completed_job = futures[future]
                    future.result()
                    _emit_event(
                        "country_generated",
                        started,
                        country=completed_job.country,
                    )

    batch_audit = {
        "status": "pass",
        "countries": {},
        "deterministic_failures": [],
        "gpt_audit": None,
        "reused_without_reaudit": [job.country for job in reused_jobs],
    }
    if new_jobs:
        audit_started = time.perf_counter()
        batch_audit = audit_report_dirs(
            {job.country: job.report_dir for job in new_jobs},
            locales=locales,
            gpt_provider=args.gpt_audit_provider,
            artifact_mode=args.artifact_mode,
        )
        _emit_event(
            "batch_audit_completed",
            started,
            stage_seconds=round(time.perf_counter() - audit_started, 3),
            status=batch_audit["status"],
        )
        batch_audit["reused_without_reaudit"] = [job.country for job in reused_jobs]
    audit_path = args.audit_output or (
        args.output_root / f"standard_report_batch_audit_{timestamp}.json"
    )
    audit_path.write_text(
        json.dumps(batch_audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if batch_audit["status"] != "pass":
        print(json.dumps(batch_audit, ensure_ascii=False, indent=2))
        return 1

    country_summaries = []
    for job in jobs:
        artifacts = _artifact_paths(
            job,
            locales,
            timestamp,
            artifact_mode=args.artifact_mode,
        )
        bundle_path = _write_bundle(job, artifacts, timestamp)
        reused_audit = (
            _latest_country_audit(job.reused_from) if job.reused_from else None
        )
        audit_result = batch_audit.get("countries", {}).get(job.country) or (
            reused_audit or {"status": "pass", "reused": True}
        )
        country_gpt_audit = (
            reused_audit.get("gpt_audit")
            if reused_audit
            else batch_audit.get("gpt_audit")
        )
        country_audit_path = (
            job.report_dir / f"standard_report_output_audit_{timestamp}.json"
        )
        country_audit_path.write_text(
            json.dumps(
                {
                    "country": job.country,
                    "content_hash": job.content_hash,
                    "status": audit_result["status"],
                    "rule_audits": audit_result.get("rule_audits", {}),
                    "gpt_audit": country_gpt_audit,
                    "reused_from": str(job.reused_from) if job.reused_from else None,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        summary = {
            "country": job.country,
            "timestamp": timestamp,
            "report_dir": str(job.report_dir),
            "source_packets": len(job.packets),
            "export_ready_packets": len(job.export_packets),
            "scene_count": len({packet.entity.scene_type for packet in job.export_packets}),
            "recommended_count": sum(
                decision.recommended
                for decision in job.recommendations[locales[0]].values()
            ),
            "content_hash": job.content_hash,
            "reused_from": str(job.reused_from) if job.reused_from else None,
            "payload_path": str(_payload_path(job, timestamp)),
            "generated_files": [str(path) for path in artifacts],
            "audit_path": str(country_audit_path),
            "bundle_path": str(bundle_path),
            "audit_status": audit_result["status"],
            "artifact_mode": args.artifact_mode,
        }
        summary_path = (
            job.report_dir / f"standard_report_generation_summary_{timestamp}.json"
        )
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        country_summaries.append(summary)

    batch_summary = {
        "timestamp": timestamp,
        "countries": countries,
        "locales": list(locales),
        "artifact_mode": args.artifact_mode,
        "new_country_count": len(new_jobs),
        "reused_country_count": len(reused_jobs),
        "audit_path": str(audit_path),
        "audit_status": batch_audit["status"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "country_reports": country_summaries,
    }
    batch_summary_path = (
        args.output_root / f"standard_report_batch_summary_{timestamp}.json"
    )
    batch_summary_path.write_text(
        json.dumps(batch_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(batch_summary, ensure_ascii=False, indent=2))
    return 0


def _build_country_job(
    country: str,
    *,
    output_root: Path,
    timestamp: str,
    locales: tuple[str, ...],
    repository,
    include_blocked_quality: bool,
    artifact_mode: str = "all",
) -> CountryJob:
    slug = _slug(country)
    report_dir = output_root / f"{slug}_standard_report_{timestamp}"
    packets = repository.list_properties({"country": country})
    export_packets = filter_packets_for_surface(
        packets,
        "export",
        include_blocked_quality=include_blocked_quality,
    )
    if not export_packets:
        raise SystemExit(f"no export-ready packets found for country={country!r}")
    recommendations = {
        locale: _build_recommendation_index(export_packets, locale=locale)
        for locale in locales
    }
    _assert_locale_recommendations_match(recommendations)
    representatives = scene_card_image_candidates(
        export_packets,
        recommendations[locales[0]],
        per_scene=3,
    )
    payload = _report_payload(
        country,
        export_packets,
        recommendations,
        locales,
        artifact_mode,
    )
    content_hash = _report_content_hash(payload, artifact_mode=artifact_mode)
    payload["content_hash"] = content_hash
    return CountryJob(
        country=country,
        slug=slug,
        report_dir=report_dir,
        packets=packets,
        export_packets=export_packets,
        recommendations=recommendations,
        representatives=representatives,
        payload=payload,
        content_hash=content_hash,
    )


def _generate_country_artifacts(
    job: CountryJob,
    locales: tuple[str, ...],
    timestamp: str,
    localization_cache: DatabaseLocalizationCache | None,
    image_assets: dict[str, Path],
    artifact_mode: str = "all",
) -> None:
    started = time.perf_counter()
    job.report_dir.mkdir(parents=True, exist_ok=True)
    _payload_path(job, timestamp).write_text(
        json.dumps(job.payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for locale in locales:
        excel_path, ppt_path = _locale_artifact_paths(job, locale, timestamp)
        write_excel_skeleton(
            excel_path,
            packets=job.export_packets,
            locale=locale,
            localization_cache=localization_cache,
            recommendations=job.recommendations[locale],
        )
        if artifact_mode == "all":
            write_ppt_deck(
                ppt_path,
                packets=job.export_packets,
                locale=locale,
                localization_cache=localization_cache,
                recommendations=job.recommendations[locale],
                image_assets=image_assets,
                require_images=True,
            )
    _emit_event(
        "country_artifacts_written",
        started,
        country=job.country,
        files=len(locales) * (2 if artifact_mode == "all" else 1),
        artifact_mode=artifact_mode,
    )


def _report_payload(
    country: str,
    packets: list[SitePacket],
    recommendations: dict[str, dict[str, RecommendationDecision]],
    locales: tuple[str, ...],
    artifact_mode: str = "all",
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_PAYLOAD_SCHEMA_VERSION,
        "generator_version": REPORT_GENERATOR_VERSION,
        "country": country,
        "locales": list(locales),
        "artifact_mode": artifact_mode,
        "packets": [
            packet.model_dump(mode="json", exclude_none=False)
            for packet in sorted(
                packets,
                key=lambda item: str(item.entity.property_id),
            )
        ],
        "recommendations": {
            locale: {
                key: asdict(decision)
                for key, decision in sorted(locale_decisions.items())
            }
            for locale, locale_decisions in sorted(recommendations.items())
        },
    }


def _missing_scene_image_candidates(
    job: CountryJob,
    image_assets: dict[str, Path],
    attempted_urls: set[str],
) -> list[SitePacket]:
    missing_scenes = set(_missing_scene_images(job, image_assets))
    return [
        packet
        for packet in job.export_packets
        if packet.entity.scene_type in missing_scenes
        and packet.entity.hero_image
        and str(packet.entity.hero_image.url) not in attempted_urls
    ]


def _missing_scene_images(
    job: CountryJob,
    image_assets: dict[str, Path],
) -> list[str]:
    displayed_scenes = set(scene_card_scene_types(job.export_packets))
    scenes_with_assets = {
        packet.entity.scene_type
        for packet in job.export_packets
        if packet.entity.scene_type in displayed_scenes
        if packet.entity.hero_image
        and str(packet.entity.hero_image.url) in image_assets
    }
    return sorted(
        displayed_scenes - scenes_with_assets
    )


def _report_content_hash(
    payload: dict[str, Any],
    *,
    artifact_mode: str = "all",
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    for path in _report_contract_paths(artifact_mode):
        try:
            contract_name = str(path.relative_to(ROOT))
        except ValueError:
            contract_name = str(path)
        digest.update(contract_name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _report_contract_paths(artifact_mode: str = "all") -> list[Path]:
    paths = [
        ROOT / "config" / "output_templates.yaml",
        ROOT / "config" / "qa_controls.yaml",
        resolve_qa_lessons_path(ROOT),
        ROOT / "src" / "isite2" / "output" / "excel.py",
        ROOT / "scripts" / "generate_standard_country_report.py",
        ROOT / "scripts" / "qa_standard_report_output_bundle.py",
        ROOT / "scripts" / "qa_standard_report_ppt_metrics.py",
    ]
    if artifact_mode == "all":
        paths.extend(
            [
                ROOT / "src" / "isite2" / "output" / "ppt.py",
                ROOT / "templates" / "ppt" / "isite_standard_one_page_zh.pptx",
                ROOT / "templates" / "ppt" / "isite_standard_one_page_en.pptx",
            ]
        )
    return paths


def _assert_locale_recommendations_match(
    recommendations: dict[str, dict[str, RecommendationDecision]],
) -> None:
    fingerprints = {
        locale: {
            key: (
                decision.recommended,
                decision.rank,
                decision.metric_key,
                decision.metric_value,
                decision.threshold_metric_key,
                decision.threshold_value,
            )
            for key, decision in decisions.items()
        }
        for locale, decisions in recommendations.items()
    }
    first = next(iter(fingerprints.values()))
    for locale, fingerprint in fingerprints.items():
        if fingerprint != first:
            raise ValueError(
                f"localized recommendation decisions diverged for locale={locale}"
            )


def _find_reusable_report(
    output_root: Path,
    job: CountryJob,
    locales: tuple[str, ...],
    *,
    artifact_mode: str = "all",
    require_gpt_audit: bool = False,
) -> Path | None:
    candidates = sorted(
        output_root.glob(f"{job.slug}_standard_report_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in candidates:
        if directory.resolve() == job.report_dir.resolve():
            continue
        summaries = sorted(
            directory.glob("standard_report_generation_summary_*.json"),
            reverse=True,
        )
        for summary_path in summaries:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if summary.get("content_hash") != job.content_hash:
                continue
            if summary.get("audit_status") != "pass":
                continue
            old_timestamp = str(summary.get("timestamp") or "")
            if not old_timestamp:
                continue
            artifacts = _artifact_paths_for(
                directory,
                job.slug,
                locales,
                old_timestamp,
                artifact_mode=artifact_mode,
            )
            if require_gpt_audit and not _gpt_audit_passed(
                _latest_country_audit(directory)
            ):
                continue
            if all(path.exists() for path in artifacts):
                return directory
    return None


def _latest_country_audit(directory: Path | None) -> dict[str, Any] | None:
    if directory is None:
        return None
    for audit_path in sorted(
        directory.glob("standard_report_output_audit_*.json"),
        reverse=True,
    ):
        try:
            return json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _gpt_audit_passed(audit: dict[str, Any] | None) -> bool:
    if not audit:
        return False
    gpt_audit = audit.get("gpt_audit")
    return bool(
        isinstance(gpt_audit, dict)
        and str(gpt_audit.get("verdict") or "").casefold() == "pass"
    )


def _reuse_country_artifacts(
    job: CountryJob,
    locales: tuple[str, ...],
    timestamp: str,
    *,
    artifact_mode: str = "all",
) -> None:
    if job.reused_from is None:
        return
    summaries = sorted(
        job.reused_from.glob("standard_report_generation_summary_*.json"),
        reverse=True,
    )
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    old_timestamp = str(summary["timestamp"])
    sources = _artifact_paths_for(
        job.reused_from,
        job.slug,
        locales,
        old_timestamp,
        artifact_mode=artifact_mode,
    )
    targets = _artifact_paths(
        job,
        locales,
        timestamp,
        artifact_mode=artifact_mode,
    )
    job.report_dir.mkdir(parents=True, exist_ok=True)
    for source, target in zip(sources, targets, strict=True):
        shutil.copy2(source, target)
    _payload_path(job, timestamp).write_text(
        json.dumps(job.payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_bundle(
    job: CountryJob,
    artifacts: list[Path],
    timestamp: str,
) -> Path:
    bundle_path = (
        job.report_dir / f"isite_{job.slug}_standard_report_bundle_{timestamp}.zip"
    )
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in artifacts:
            archive.write(path, arcname=path.name)
    with zipfile.ZipFile(bundle_path) as archive:
        bad_member = archive.testzip()
    if bad_member is not None:
        raise RuntimeError(f"zip integrity check failed at {bad_member}")
    return bundle_path


def _artifact_paths(
    job: CountryJob,
    locales: tuple[str, ...],
    timestamp: str,
    *,
    artifact_mode: str = "all",
) -> list[Path]:
    return _artifact_paths_for(
        job.report_dir,
        job.slug,
        locales,
        timestamp,
        artifact_mode=artifact_mode,
    )


def _artifact_paths_for(
    report_dir: Path,
    slug: str,
    locales: tuple[str, ...],
    timestamp: str,
    *,
    artifact_mode: str = "all",
) -> list[Path]:
    paths: list[Path] = []
    for locale in locales:
        paths.append(
            report_dir / f"isite_{slug}_standard_report_{locale}_{timestamp}.xlsx"
        )
        if artifact_mode == "all":
            paths.append(
                report_dir / f"isite_{slug}_standard_ppt_{locale}_{timestamp}.pptx"
            )
    return paths


def _locale_artifact_paths(
    job: CountryJob,
    locale: str,
    timestamp: str,
) -> tuple[Path, Path]:
    return (
        job.report_dir
        / f"isite_{job.slug}_standard_report_{locale}_{timestamp}.xlsx",
        job.report_dir / f"isite_{job.slug}_standard_ppt_{locale}_{timestamp}.pptx",
    )


def _payload_path(job: CountryJob, timestamp: str) -> Path:
    return job.report_dir / f"standard_report_payload_{timestamp}.json"


def _localization_cache(repository) -> DatabaseLocalizationCache | None:
    engine = getattr(repository, "engine", None)
    return DatabaseLocalizationCache(engine) if engine is not None else None


def _slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug or "country"


def _emit_event(event: str, started: float, **details: Any) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                **details,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    sys.exit(main())
