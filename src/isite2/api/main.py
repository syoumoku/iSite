from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from isite2.connectors import HttpPublicEvidenceProvider
from isite2.connectors.extraction import extract_first_indicator
from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.growth.evidence_intake import load_effective_source_registry
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.regional_targets import DEFAULT_REGIONS, countries_for_regions
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.output.excel import write_excel_skeleton
from isite2.output.ppt import write_ppt_deck
from isite2.rag import RagService
from isite2.repositories import get_default_repository
from isite2.rules.candidate_quality import filter_packets_for_surface
from isite2.rules.config_loader import load_output_template, load_scene_rules
from isite2.rules.coordinates import is_map_ready_coordinate
from isite2.rules.validation import display_slice

PUBLIC_VIEW_MODE = "public_view"


def app_mode() -> str:
    return os.getenv("ISITE2_APP_MODE", "local").strip() or "local"


def is_public_view() -> bool:
    return app_mode() == PUBLIC_VIEW_MODE


def runtime_features() -> dict[str, bool]:
    public_view = is_public_view()
    return {
        "exports": not public_view,
        "rag": not public_view,
        "connectors": not public_view,
        "geocode": not public_view,
    }


app = FastAPI(title="iSite2 API", version="0.1.0")
app.state.overlay_sync_enabled = (
    os.getenv("ISITE2_ENABLE_OVERLAY_SYNC", "1") != "0" and not is_public_view()
)
app.state.overlay_sync_checked_at = 0.0
app.state.overlay_sync_running = False
repository = get_default_repository()
curation_store = EvidenceCurationStore.from_repository(repository)
evidence_provider = HttpPublicEvidenceProvider()
rag_service = RagService(repository.engine)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_UI_DIR = _PROJECT_ROOT / "ui" / "world_map"
_UI_DIST_DIR = _UI_DIR / "dist"
_OVERLAY_SYNC_LOCK = threading.Lock()
_OVERLAY_SYNC_TTL_SECONDS = 300.0
_DEFAULT_VERSATILES_TILE_TEMPLATE = "https://tiles.versatiles.org/tiles/satellite/{z}/{x}/{y}"
_DEFAULT_MAPTILER_TILE_TEMPLATE = (
    "https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={token}"
)
_DEFAULT_MAPTILER_TILE_TOKEN = "rnpOwVToJlpNtgw35V2t"
_VERSATILES_SATELLITE_ATTRIBUTION = "Source: VersaTiles Satellite"
_MAPTILER_SATELLITE_ATTRIBUTION = "Source: MapTiler Satellite"
_SATELLITE_TILE_TTL_SECONDS = 14 * 24 * 60 * 60
_SATELLITE_TILE_FAILURE_TTL_SECONDS = 5 * 60
_SATELLITE_TILE_TIMEOUT_SECONDS = 4.0
_SATELLITE_TILE_BREAKER_TTL_SECONDS = 5 * 60
_SATELLITE_TILE_BREAKER_THRESHOLD = 3
_SATELLITE_TILE_FATAL_STATUS_CODES = {401, 403, 429}
_SATELLITE_TILE_LOCK = threading.Lock()
_SATELLITE_TILE_BREAKER = {
    "consecutive_failures": 0,
    "opened_until": 0.0,
    "reason": "",
}


@app.middleware("http")
async def enforce_public_view_read_only(request: Request, call_next):
    if is_public_view() and not _public_view_request_allowed(request):
        return JSONResponse(
            {"detail": "endpoint disabled in public_view mode"},
            status_code=403,
        )
    return await call_next(request)


@app.middleware("http")
async def prevent_cached_ui_assets(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/ui" or path == "/ui/":
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.startswith("/ui/assets/") or path.startswith("/ui/earth-"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if "Pragma" in response.headers:
            del response.headers["Pragma"]
        if "Expires" in response.headers:
            del response.headers["Expires"]
    return response


def _public_view_request_allowed(request: Request) -> bool:
    if request.method not in {"GET", "HEAD"}:
        return False

    path = request.url.path
    if path in {"/health", "/runtime-config", "/review-queue", "/discovery/status"}:
        return True
    if path == "/ui" or path.startswith("/ui/"):
        return True
    if path.startswith("/rules/"):
        return True
    if path == "/scan-runs" or path.startswith("/scan-runs/"):
        return True
    if path.startswith("/map/"):
        return True
    if path == "/properties" or path.startswith("/properties/"):
        return True
    return False


class CreateScanRunRequest(BaseModel):
    scope: dict


class OutputRequest(BaseModel):
    scan_run_id: UUID | None = None
    country: str | None = None
    city: str | None = None
    scene_type: str | None = None
    evidence_status: str | None = None
    value_class: str | None = None
    action_class: str | None = None
    recommended_solution: str | None = None
    indoor_system_presence: str | None = None
    indoor_rat: str | None = None
    proxy_level: str | None = None
    has_review_issue: bool | None = None
    candidate_quality_status: str | None = None
    include_blocked_quality: bool = False


class RagIndexRequest(BaseModel):
    scan_run_id: UUID | None = None
    property_id: UUID | None = None
    country: str | None = None


class RagQueryRequest(BaseModel):
    question: str
    scan_run_id: UUID | None = None
    property_id: UUID | None = None
    country: str | None = None
    scene_type: str | None = None
    top_k: int = 5


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/runtime-config")
def runtime_config() -> dict:
    return {
        "mode": app_mode(),
        "features": runtime_features(),
        "map": {
            "satelliteTileTemplate": "/map/satellite-tiles/{z}/{y}/{x}",
            "satelliteTileSize": _satellite_tile_size(),
            "satelliteAttribution": _satellite_tile_attribution(),
        },
    }


@app.on_event("startup")
def startup_overlay_sync() -> None:
    _ensure_overlay_registry_synced()


@app.post("/scan-runs", status_code=201)
def create_scan_run(request: CreateScanRunRequest) -> dict:
    _ensure_overlay_registry_synced()
    scope, source_registry = _scan_scope_and_registry(request.scope)
    result = run_scan_pipeline(scope, repository, source_registry=source_registry)
    derived_refresh = refresh_derived_after_scan(repository, result.scan_run.run_id)
    _clear_ui_caches()
    response = _scan_run_response(result)
    response["derived_refresh"] = derived_refresh
    return jsonable_encoder(response)


@app.get("/scan-runs")
def list_scan_runs() -> list[dict]:
    _ensure_overlay_registry_synced()
    return [
        jsonable_encoder(_scan_run_response(result, include_packets=False))
        for result in repository.list()
    ]


@app.get("/scan-runs/{run_id}")
def get_scan_run(run_id: UUID, top_n: int | None = Query(default=None, ge=1)) -> dict:
    result = repository.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="scan run not found")
    return jsonable_encoder(_scan_run_response(result, top_n=top_n))


@app.get("/rules/scenes")
def get_scene_rules() -> dict:
    return load_scene_rules()


@app.get("/rules/output-template")
def get_output_template() -> dict:
    return load_output_template()


@app.get("/properties")
def list_properties(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    evidence_status: str | None = None,
    value_class: str | None = None,
    action_class: str | None = None,
    recommended_solution: str | None = None,
    indoor_system_presence: str | None = None,
    indoor_rat: str | None = None,
    proxy_level: str | None = None,
    has_review_issue: bool | None = None,
    candidate_quality_status: str | None = None,
    include_blocked_quality: bool = False,
    top_n: int | None = Query(default=None, ge=1),
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        scene_type=scene_type,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        recommended_solution=recommended_solution,
        indoor_system_presence=indoor_system_presence,
        indoor_rat=indoor_rat,
        proxy_level=proxy_level,
        has_review_issue=has_review_issue,
        candidate_quality_status=candidate_quality_status,
    )
    packets = repository.list_properties(filters)
    packets = filter_packets_for_surface(
        packets,
        "main_table",
        include_blocked_quality=include_blocked_quality or candidate_quality_status is not None,
    )
    visible_packets = display_slice(packets, top_n)
    return jsonable_encoder(
        {
            "candidate_count": len(packets),
            "display_count": len(visible_packets),
            "packets": visible_packets,
        }
    )


@app.get("/properties/{property_id}")
def get_property(property_id: UUID) -> dict:
    _ensure_overlay_registry_synced()
    packet = repository.get_property(property_id)
    if packet is None:
        raise HTTPException(status_code=404, detail="property not found")
    return jsonable_encoder(packet)


@app.get("/review-queue")
def list_review_queue(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    evidence_status: str | None = None,
    value_class: str | None = None,
    action_class: str | None = None,
    indoor_system_presence: str | None = None,
    status: str | None = None,
) -> list[dict]:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        scene_type=scene_type,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        indoor_system_presence=indoor_system_presence,
        candidate_quality_status=None,
    )
    rows = []
    for packet in repository.list_properties(filters):
        for review in packet.review_queue:
            if status and review.status != status:
                continue
            rows.append(
                {
                    "property_id": packet.entity.property_id,
                    "property_name": packet.entity.property_name,
                    "country": packet.entity.country,
                    "city": packet.entity.city,
                    "scene_type": packet.entity.scene_type,
                    "candidate_quality_status": packet.candidate_quality_status,
                    "visibility": packet.visibility,
                    "quality_issues": packet.quality_issues,
                    "reason": review.reason,
                    "next_action": review.next_action,
                    "status": review.status,
                    "review_type": review.review_type,
                    "severity": review.severity,
                    "gate_name": review.gate_name,
                    "field_path": review.field_path,
                    "blocking_surfaces": review.blocking_surfaces,
                    "source_url": review.source_url,
                    "suggested_query": review.suggested_query,
                }
            )
    return jsonable_encoder(rows)


@app.get("/raw-evidence")
def list_raw_evidence(
    status: str | None = None,
    country: str | None = None,
    scene_type: str | None = None,
    source_type: str | None = None,
    curation_run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    rows = curation_store.list_raw_evidence(
        status=status,
        country=country,
        scene_type=scene_type,
        source_type=source_type,
        curation_run_id=curation_run_id,
        limit=limit,
    )
    return jsonable_encoder({"count": len(rows), "items": rows})


@app.get("/candidate-drafts")
def list_candidate_drafts(
    status: str | None = None,
    country: str | None = None,
    scene_type: str | None = None,
    source_type: str | None = None,
    curation_run_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    rows = curation_store.list_candidate_drafts(
        status=status,
        country=country,
        scene_type=scene_type,
        source_type=source_type,
        curation_run_id=curation_run_id,
        limit=limit,
    )
    return jsonable_encoder({"count": len(rows), "items": rows})


@app.get("/discovery/status")
def discovery_status() -> dict:
    return jsonable_encoder(curation_store.discovery_status())


@app.post("/rag/index", status_code=202)
def rag_index(request: RagIndexRequest) -> dict:
    result = rag_service.index(
        scan_run_id=request.scan_run_id,
        property_id=request.property_id,
        country=request.country,
    )
    return jsonable_encoder(result)


@app.post("/rag/query")
def rag_query(request: RagQueryRequest) -> dict:
    result = rag_service.query(
        question=request.question,
        scan_run_id=request.scan_run_id,
        property_id=request.property_id,
        country=request.country,
        scene_type=request.scene_type,
        top_k=request.top_k,
    )
    return jsonable_encoder(result)


@app.get("/map/properties")
def map_properties(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    evidence_status: str | None = None,
    value_class: str | None = None,
    action_class: str | None = None,
    indoor_system_presence: str | None = None,
    indoor_rat: str | None = None,
    proxy_level: str | None = None,
    has_review_issue: bool | None = None,
    candidate_quality_status: str | None = None,
    include_blocked_quality: bool = False,
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        scene_type=scene_type,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        indoor_system_presence=indoor_system_presence,
        indoor_rat=indoor_rat,
        proxy_level=proxy_level,
        has_review_issue=has_review_issue,
        candidate_quality_status=candidate_quality_status,
    )
    features = []
    last_scan_at = _latest_scan_at()
    packets = repository.list_properties(filters)
    packets = filter_packets_for_surface(
        packets,
        "map",
        include_blocked_quality=include_blocked_quality or candidate_quality_status is not None,
    )
    for packet in packets:
        entity = packet.entity
        if entity.longitude is None or entity.latitude is None:
            continue
        if not is_map_ready_coordinate(entity.coordinate_status):
            continue
        source_urls = {str(evidence.source_url) for evidence in packet.evidence}
        hero_image = entity.hero_image
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [entity.longitude, entity.latitude],
                },
                "properties": {
                    "property_id": str(entity.property_id),
                    "property_name": entity.property_name,
                    "country": entity.country,
                    "city": entity.city,
                    "scene_type": entity.scene_type,
                    "evidence_status": packet.conclusion.evidence_status,
                    "value_class": packet.conclusion.value_class,
                    "action_class": packet.conclusion.action_class,
                    "recommended_solution": packet.conclusion.recommended_solution,
                    "main_metric_text": packet.evidence[0].field_value if packet.evidence else "",
                    "annual_visits_est": packet.scene.annual_visits_est,
                    "busy_hour_traffic_gb": (
                        packet.demand.busy_hour_traffic_gb if packet.demand else None
                    ),
                    "review_count": len(packet.review_queue),
                    "source_count": len(source_urls),
                    "indoor_system_presence": packet.build_status.indoor_system_presence,
                    "indoor_rat": packet.build_status.indoor_rat,
                    "proxy_level": packet.scene.proxy_level,
                    "last_scan_at": last_scan_at,
                    "google_maps_link": entity.google_maps_link,
                    "geocode_precision": entity.geocode_precision,
                    "map_source": entity.map_source,
                    "coordinate_status": entity.coordinate_status,
                    "candidate_quality_status": packet.candidate_quality_status,
                    "visibility": packet.visibility,
                    "quality_issues": packet.quality_issues,
                    "hero_image_url": str(hero_image.url) if hero_image else None,
                    "hero_image_alt": hero_image.alt_text if hero_image else None,
                    "hero_image_source_name": (
                        hero_image.source_name if hero_image else None
                    ),
                },
            }
        )
    return jsonable_encoder({"type": "FeatureCollection", "features": features})


@app.get("/map/country-summary")
def country_summary(scan_run_id: UUID | None = None) -> list[dict]:
    return _country_summary_rows(scan_run_id=scan_run_id)


@app.get("/map/satellite-tiles/{z}/{y}/{x}")
def satellite_tile(z: int, y: int, x: int) -> Response:
    _validate_satellite_tile_coordinates(z, y, x)
    content, content_type = _satellite_tile_from_cache_or_upstream(z, y, x)
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=604800",
            "X-iSite2-Tile-Source": "cache-or-upstream",
        },
    )


@app.get("/map/city-summary")
def city_summary(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    scene_type: str | None = None,
    evidence_status: str | None = None,
    value_class: str | None = None,
    action_class: str | None = None,
    indoor_system_presence: str | None = None,
    indoor_rat: str | None = None,
    proxy_level: str | None = None,
    has_review_issue: bool | None = None,
    candidate_quality_status: str | None = None,
    include_blocked_quality: bool = False,
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        scene_type=scene_type,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        indoor_system_presence=indoor_system_presence,
        indoor_rat=indoor_rat,
        proxy_level=proxy_level,
        has_review_issue=has_review_issue,
        candidate_quality_status=candidate_quality_status,
    )
    include_blocked = include_blocked_quality or candidate_quality_status is not None
    summary_loader = getattr(repository, "list_city_summaries", None)
    if callable(summary_loader):
        rows = summary_loader(filters, include_blocked_quality=include_blocked)
    else:
        packets = filter_packets_for_surface(
            repository.list_properties(filters),
            "main_table",
            include_blocked_quality=include_blocked,
        )
        rows = _city_summary_rows_from_packets(packets)
    return jsonable_encoder({"cities": rows})


def _country_summary_rows(scan_run_id: UUID | None = None) -> list[dict]:
    _ensure_overlay_registry_synced()
    summary_loader = getattr(repository, "list_country_summaries", None)
    if callable(summary_loader):
        return summary_loader(scan_run_id=scan_run_id)
    summary: dict[str, dict] = {}
    filters = _property_filters(scan_run_id=scan_run_id)
    for packet in filter_packets_for_surface(repository.list_properties(filters), "main_table"):
        country = packet.entity.country
        row = summary.setdefault(
            country,
            {
                "country": country,
                "candidate_count": 0,
                "map_point_count": 0,
                "coordinate_review_count": 0,
                "review_count": 0,
                "source_count": 0,
                "scenes": {},
                "_source_urls": set(),
            },
        )
        row["candidate_count"] += 1
        if (
            packet.entity.longitude is not None
            and packet.entity.latitude is not None
            and is_map_ready_coordinate(packet.entity.coordinate_status)
        ):
            row["map_point_count"] += 1
        if packet.entity.coordinate_status == "Review Required":
            row["coordinate_review_count"] += 1
        row["review_count"] += len(packet.review_queue)
        row["_source_urls"].update(str(evidence.source_url) for evidence in packet.evidence)
        row["scenes"][packet.entity.scene_type] = row["scenes"].get(packet.entity.scene_type, 0) + 1
    rows = []
    for row in summary.values():
        row["source_count"] = len(row.pop("_source_urls"))
        rows.append(row)
    return rows


def _city_summary_rows_from_packets(packets: list) -> list[dict]:
    summary: dict[str, dict] = {}
    for packet in packets:
        entity = packet.entity
        if not entity.city.strip():
            continue
        key = f"{entity.country.strip().lower()}::{entity.city.strip().lower()}"
        row = summary.setdefault(
            key,
            {
                "country": entity.country,
                "city": entity.city,
                "candidate_count": 0,
                "map_point_count": 0,
                "review_count": 0,
                "source_count": 0,
                "scenes": {},
                "property_ids": [],
                "_source_urls": set(),
                "_map_lat_total": 0.0,
                "_map_lng_total": 0.0,
                "_map_count": 0,
                "_all_lat_total": 0.0,
                "_all_lng_total": 0.0,
                "_all_count": 0,
            },
        )
        row["candidate_count"] += 1
        row["review_count"] += len(packet.review_queue)
        row["property_ids"].append(str(entity.property_id))
        row["scenes"][entity.scene_type] = row["scenes"].get(entity.scene_type, 0) + 1
        row["_source_urls"].update(str(evidence.source_url) for evidence in packet.evidence)
        if entity.latitude is None or entity.longitude is None:
            continue
        row["_all_lat_total"] += entity.latitude
        row["_all_lng_total"] += entity.longitude
        row["_all_count"] += 1
        if is_map_ready_coordinate(entity.coordinate_status):
            row["map_point_count"] += 1
            row["_map_lat_total"] += entity.latitude
            row["_map_lng_total"] += entity.longitude
            row["_map_count"] += 1

    rows = []
    for row in summary.values():
        map_count = row.pop("_map_count")
        all_count = row.pop("_all_count")
        source_urls = row.pop("_source_urls")
        map_lat_total = row.pop("_map_lat_total")
        map_lng_total = row.pop("_map_lng_total")
        all_lat_total = row.pop("_all_lat_total")
        all_lng_total = row.pop("_all_lng_total")
        if map_count > 0:
            row["lat"] = map_lat_total / map_count
            row["lng"] = map_lng_total / map_count
            row["position_source"] = "map_ready_average"
        elif all_count > 0:
            row["lat"] = all_lat_total / all_count
            row["lng"] = all_lng_total / all_count
            row["position_source"] = "property_average"
        else:
            continue
        row["source_count"] = len(source_urls)
        rows.append(row)
    return sorted(rows, key=lambda item: (-item["candidate_count"], item["country"], item["city"]))


@app.get("/connectors/search")
def connector_search(q: str, limit: int = Query(default=5, ge=1, le=10)) -> list[dict]:
    return jsonable_encoder(evidence_provider.search(q, limit=limit))


@app.get("/connectors/fetch")
def connector_fetch(url: str) -> dict:
    page = evidence_provider.fetch_page(url)
    return jsonable_encoder(page)


@app.get("/connectors/geocode")
def connector_geocode(q: str) -> dict:
    return jsonable_encoder(evidence_provider.geocode(q))


@app.get("/connectors/extract")
def connector_extract(url: str, property_name: str, indicators: str) -> dict:
    page = evidence_provider.fetch_page(url)
    result = extract_first_indicator(page, property_name, indicators.split(","))
    return jsonable_encoder(result)


@app.post("/outputs/excel", status_code=202)
def generate_excel(request: OutputRequest | None = None) -> dict:
    request = request or OutputRequest()
    packets, path, artifact_base = _output_payload(request, "excel", "xlsx")
    path = write_excel_skeleton(
        path=path,
        packets=packets,
        include_blocked_quality=(
            request.include_blocked_quality or request.candidate_quality_status is not None
        ),
    )
    artifact = _output_artifact(request, "excel", path, artifact_base)
    return jsonable_encoder(artifact)


@app.post("/outputs/ppt", status_code=202)
def generate_ppt(request: OutputRequest | None = None) -> dict:
    request = request or OutputRequest()
    packets, path, artifact_base = _output_payload(request, "ppt", "pptx")
    path = write_ppt_deck(
        path=path,
        packets=packets,
        include_blocked_quality=(
            request.include_blocked_quality or request.candidate_quality_status is not None
        ),
    )
    artifact = _output_artifact(request, "ppt", path, artifact_base)
    return jsonable_encoder(artifact)


def _scan_run_response(result, include_packets: bool = True, top_n: int | None = None) -> dict:
    packets = display_slice(result.packets, top_n)
    response = {
        "run_id": result.scan_run.run_id,
        "status": result.scan_run.status,
        "created_at": result.scan_run.created_at,
        "scope": result.scan_run.scope,
        "candidate_count": result.scan_run.candidate_count,
        "review_count": result.scan_run.review_count,
        "storage_mode": result.storage_mode,
        "persisted_counts": result.persisted_counts,
    }
    if include_packets:
        response["packets"] = packets
        response["display_count"] = len(packets)
        response["artifacts"] = repository.list_output_artifacts(result.scan_run.run_id)
    return response


def _scan_scope_and_registry(scope: dict) -> tuple[dict, dict | None]:
    """Expand UI regional scans to the runtime registry-backed candidate pool."""
    expanded_scope = dict(scope)
    custom_filters = dict(expanded_scope.get("custom_filters") or {})
    if not custom_filters.get("registry_backed_only"):
        return expanded_scope, None

    source_registry = load_effective_source_registry()
    if not expanded_scope.get("countries"):
        expanded_scope["countries"] = _registry_backed_countries(
            expanded_scope.get("regions") or list(DEFAULT_REGIONS),
            source_registry,
        )
    expanded_scope["custom_filters"] = custom_filters
    return expanded_scope, source_registry


def _ensure_overlay_registry_synced() -> None:
    if is_public_view():
        return
    if not getattr(app.state, "overlay_sync_enabled", True):
        return
    now = time.monotonic()
    if now - getattr(app.state, "overlay_sync_checked_at", 0.0) < _OVERLAY_SYNC_TTL_SECONDS:
        return
    if getattr(app.state, "overlay_sync_running", False):
        return
    app.state.overlay_sync_running = True
    thread = threading.Thread(target=_sync_overlay_registry_worker, daemon=True)
    thread.start()


def _sync_overlay_registry_worker() -> None:
    try:
        _sync_overlay_registry_now()
    finally:
        app.state.overlay_sync_running = False


def _sync_overlay_registry_now() -> None:
    with _OVERLAY_SYNC_LOCK:
        result = sync_overlay_to_active_repository(repository)
        app.state.overlay_sync_checked_at = time.monotonic()
        if result.created:
            _clear_ui_caches()


def _clear_ui_caches() -> None:
    # Summary endpoints are recomputed per request because growth/import jobs can
    # update the active DB outside this API process.
    return None


class _SatelliteTileUpstreamError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _satellite_tile_token() -> str:
    return os.getenv("ISITE2_SATELLITE_TILE_TOKEN", _DEFAULT_MAPTILER_TILE_TOKEN).strip()


def _satellite_tile_template() -> str:
    configured = os.getenv("ISITE2_SATELLITE_TILE_TEMPLATE", "").strip()
    if configured:
        return configured
    if _satellite_tile_token():
        return _DEFAULT_MAPTILER_TILE_TEMPLATE
    return _DEFAULT_VERSATILES_TILE_TEMPLATE


def _satellite_tile_size() -> int:
    raw = os.getenv("ISITE2_SATELLITE_TILE_SIZE", "512").strip()
    try:
        value = int(raw)
    except ValueError:
        return 512
    return value if value > 0 else 512


def _satellite_tile_attribution() -> str:
    configured = os.getenv("ISITE2_SATELLITE_TILE_ATTRIBUTION", "").strip()
    if configured:
        return configured
    template = _satellite_tile_template().casefold()
    if "maptiler" in template:
        return _MAPTILER_SATELLITE_ATTRIBUTION
    if "versatiles" in template:
        return _VERSATILES_SATELLITE_ATTRIBUTION
    return "Source: configured satellite imagery provider"


def _satellite_tile_url(z: int, y: int, x: int) -> str:
    return _satellite_tile_template().format(
        z=z,
        y=y,
        x=x,
        token=_satellite_tile_token(),
    )


def _satellite_tile_cache_dir() -> Path:
    return Path(
        os.getenv(
            "ISITE2_TILE_CACHE_DIR",
            str(_PROJECT_ROOT / ".tmp" / "isite2_tile_cache"),
        )
    )


def _validate_satellite_tile_coordinates(z: int, y: int, x: int) -> None:
    if z < 0 or z > 18:
        raise HTTPException(status_code=400, detail="satellite tile zoom must be between 0 and 18")
    max_index = (2**z) - 1
    if x < 0 or y < 0 or x > max_index or y > max_index:
        raise HTTPException(status_code=400, detail="satellite tile x/y is outside zoom bounds")


def _satellite_tile_cache_paths(z: int, y: int, x: int) -> tuple[Path, Path, Path]:
    cache_source = f"{_satellite_tile_template()}|tileSize={_satellite_tile_size()}"
    template_hash = hashlib.sha256(cache_source.encode("utf-8")).hexdigest()[:16]
    base = _satellite_tile_cache_dir() / template_hash / str(z) / str(y)
    return base / f"{x}.tile", base / f"{x}.json", base / f"{x}.fail"


def _satellite_tile_from_cache_or_upstream(z: int, y: int, x: int) -> tuple[bytes, str]:
    tile_path, meta_path, fail_path = _satellite_tile_cache_paths(z, y, x)
    now = time.time()

    if tile_path.exists() and now - tile_path.stat().st_mtime <= _SATELLITE_TILE_TTL_SECONDS:
        return tile_path.read_bytes(), _satellite_tile_content_type(meta_path)

    if fail_path.exists() and now - fail_path.stat().st_mtime <= _SATELLITE_TILE_FAILURE_TTL_SECONDS:
        raise HTTPException(status_code=502, detail="satellite tile upstream recently failed")

    breaker_reason = _satellite_tile_breaker_reason(now)
    if breaker_reason:
        raise HTTPException(
            status_code=503,
            detail=f"satellite tile upstream breaker open: {breaker_reason}",
        )

    url = _satellite_tile_url(z, y, x)
    try:
        content, content_type = _fetch_satellite_tile_from_upstream(url)
    except Exception as exc:  # noqa: BLE001 - convert transport errors into a stable tile response
        fail_path.parent.mkdir(parents=True, exist_ok=True)
        fail_path.write_text(str(exc), encoding="utf-8")
        _record_satellite_tile_failure(exc)
        raise HTTPException(status_code=502, detail="satellite tile upstream failed") from exc

    _record_satellite_tile_success()
    tile_path.parent.mkdir(parents=True, exist_ok=True)
    tile_path.write_bytes(content)
    meta_path.write_text(json.dumps({"content_type": content_type}), encoding="utf-8")
    if fail_path.exists():
        fail_path.unlink()
    return content, content_type


def _satellite_tile_content_type(meta_path: Path) -> str:
    if not meta_path.exists():
        return "image/jpeg"
    try:
        value = json.loads(meta_path.read_text(encoding="utf-8")).get("content_type")
    except json.JSONDecodeError:
        return "image/jpeg"
    return value if isinstance(value, str) and value.startswith("image/") else "image/jpeg"


def _fetch_satellite_tile_from_upstream(url: str) -> tuple[bytes, str]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": "iSite2/0.1 satellite-tile-cache",
    }
    with httpx.Client(follow_redirects=True, timeout=_SATELLITE_TILE_TIMEOUT_SECONDS) as client:
        response = client.get(url, headers=headers)
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.status_code != 200 or not response.content or not content_type.startswith("image/"):
        raise _SatelliteTileUpstreamError(
            f"upstream tile failed with status={response.status_code} content_type={content_type or 'unknown'}",
            status_code=response.status_code,
        )
    return response.content, content_type


def _satellite_tile_breaker_reason(now: float | None = None) -> str | None:
    current_time = now if now is not None else time.time()
    with _SATELLITE_TILE_LOCK:
        opened_until = float(_SATELLITE_TILE_BREAKER["opened_until"])
        if opened_until > current_time:
            return str(_SATELLITE_TILE_BREAKER["reason"])
        if opened_until:
            _reset_satellite_tile_breaker_locked()
        return None


def _record_satellite_tile_success() -> None:
    with _SATELLITE_TILE_LOCK:
        _reset_satellite_tile_breaker_locked()


def _record_satellite_tile_failure(exc: Exception) -> None:
    status_code = exc.status_code if isinstance(exc, _SatelliteTileUpstreamError) else None
    should_open_immediately = status_code in _SATELLITE_TILE_FATAL_STATUS_CODES
    should_count = status_code is None or status_code >= 500 or should_open_immediately
    if not should_count:
        return

    with _SATELLITE_TILE_LOCK:
        failures = int(_SATELLITE_TILE_BREAKER["consecutive_failures"]) + 1
        _SATELLITE_TILE_BREAKER["consecutive_failures"] = failures
        if should_open_immediately or failures >= _SATELLITE_TILE_BREAKER_THRESHOLD:
            _SATELLITE_TILE_BREAKER["opened_until"] = time.time() + _SATELLITE_TILE_BREAKER_TTL_SECONDS
            _SATELLITE_TILE_BREAKER["reason"] = (
                f"status {status_code}" if status_code is not None else "transport failure"
            )


def _reset_satellite_tile_breaker() -> None:
    with _SATELLITE_TILE_LOCK:
        _reset_satellite_tile_breaker_locked()


def _reset_satellite_tile_breaker_locked() -> None:
    _SATELLITE_TILE_BREAKER["consecutive_failures"] = 0
    _SATELLITE_TILE_BREAKER["opened_until"] = 0.0
    _SATELLITE_TILE_BREAKER["reason"] = ""


def _registry_backed_countries(regions: list[str], source_registry: dict) -> list[str]:
    countries = []
    seen = set()
    for country in countries_for_regions(regions):
        if country.casefold() in seen:
            continue
        if _find_registry_country(country, source_registry) is None:
            continue
        seen.add(country.casefold())
        countries.append(country)
    return countries


def _find_registry_country(country: str, source_registry: dict) -> dict | None:
    normalized = country.casefold()
    for canonical, registry in source_registry.get("countries", {}).items():
        aliases = [canonical, *registry.get("aliases", [])]
        if normalized in {alias.casefold() for alias in aliases}:
            return registry
    return None


def _property_filters(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value is not None and value != ""}


def _output_payload(
    request: OutputRequest,
    artifact_type: str,
    suffix: str,
) -> tuple[list, Path, dict]:
    filters = _property_filters(
        country=request.country,
        city=request.city,
        scene_type=request.scene_type,
        evidence_status=request.evidence_status,
        value_class=request.value_class,
        action_class=request.action_class,
        recommended_solution=request.recommended_solution,
        indoor_system_presence=request.indoor_system_presence,
        indoor_rat=request.indoor_rat,
        proxy_level=request.proxy_level,
        has_review_issue=request.has_review_issue,
        candidate_quality_status=request.candidate_quality_status,
    )
    if request.scan_run_id is not None:
        result = repository.get(request.scan_run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="scan run not found")
        packets = [
            packet
            for packet in result.packets
            if _packet_matches_output_filters(packet, filters)
        ]
        packets = filter_packets_for_surface(
            packets,
            "export",
            include_blocked_quality=request.include_blocked_quality
            or request.candidate_quality_status is not None,
        )
        return packets, _output_path(request.scan_run_id, suffix), {
            "candidate_count": len(packets),
            "filter_snapshot": {"scan_run_id": str(request.scan_run_id), **filters},
        }

    packets = repository.list_properties(filters)
    packets = filter_packets_for_surface(
        packets,
        "export",
        include_blocked_quality=request.include_blocked_quality
        or request.candidate_quality_status is not None,
    )
    return packets, _database_output_path(artifact_type, suffix), {
        "candidate_count": len(packets),
        "filter_snapshot": filters,
    }


def _output_artifact(
    request: OutputRequest,
    artifact_type: str,
    path: Path,
    artifact_base: dict,
) -> dict:
    if request.scan_run_id is not None:
        artifact = repository.add_output_artifact(request.scan_run_id, artifact_type, str(path))
        return {**artifact, **artifact_base}
    return {
        "artifact_type": artifact_type,
        "path": str(path),
        **artifact_base,
    }


def _packet_matches_output_filters(packet, filters: dict) -> bool:
    checks = {
        "country": packet.entity.country,
        "city": packet.entity.city,
        "scene_type": packet.entity.scene_type,
        "evidence_status": packet.conclusion.evidence_status,
        "value_class": packet.conclusion.value_class,
        "action_class": packet.conclusion.action_class,
        "recommended_solution": packet.conclusion.recommended_solution,
        "indoor_system_presence": packet.build_status.indoor_system_presence,
        "indoor_rat": packet.build_status.indoor_rat,
        "proxy_level": packet.scene.proxy_level,
        "has_review_issue": bool(packet.review_queue),
        "candidate_quality_status": packet.candidate_quality_status,
    }
    return all(str(checks[key]) == str(value) for key, value in filters.items() if key in checks)


def _latest_scan_at() -> str | None:
    runs = repository.list()
    if runs:
        return runs[-1].scan_run.created_at.isoformat()
    return None


def _output_path(run_id: UUID, suffix: str):
    return Path("outputs") / f"isite2_{run_id}.{suffix}"


def _database_output_path(artifact_type: str, suffix: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("outputs") / f"isite2_current_database_{artifact_type}_{timestamp}.{suffix}"


if _UI_DIST_DIR.exists():
    app.mount("/ui", StaticFiles(directory=_UI_DIST_DIR, html=True), name="ui")
elif _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=_UI_DIR, html=True), name="ui")
