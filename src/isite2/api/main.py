from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from isite2.connectors import HttpPublicEvidenceProvider
from isite2.connectors.extraction import extract_first_indicator
from isite2.db.models import (
    NetworkPerformanceObservationDB,
    NetworkPerformanceTileDB,
    PropertyDB,
    PropertyNetworkPerformanceRollupDB,
)
from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.growth.evidence_intake import load_effective_source_registry
from isite2.growth.evidence_store import EvidenceCurationStore
from isite2.growth.footfall import (
    cached_footfall_overlay,
    footfall_cells_to_feature_collection,
    footfall_runtime_status,
)
from isite2.growth.ookla import quadkey_to_tile_xy
from isite2.growth.overlay_sync import sync_overlay_to_active_repository
from isite2.growth.regional_targets import DEFAULT_REGIONS, countries_for_regions
from isite2.localization import (
    DatabaseLocalizationCache,
    default_locale,
    enum_label,
    generic_free_text_fallback,
    localization_payload,
    localize_packet,
    resolve_locale,
    scene_label,
    supported_locales,
)
from isite2.media.hero_images import hero_image_url_variants, stable_hero_image_url
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.output.excel import write_excel_skeleton
from isite2.output.ppt import write_ppt_deck
from isite2.public_reports import CountryReportUnavailable, resolve_country_excel_report
from isite2.rag import RagService
from isite2.repositories import get_default_repository
from isite2.rules.candidate_quality import filter_packets_for_surface
from isite2.rules.scan_maturity import add_scan_maturity_to_country_summaries
from isite2.rules.config_loader import load_output_template, load_scene_rules
from isite2.rules.coordinates import is_map_ready_coordinate
from isite2.rules.validation import display_slice
from isite2.service_requests import (
    CompletionPrepare,
    DeliveryResult,
    RequestBlock,
    RequestDecision,
    ScanEnhancementRequest,
    ServiceRequestCreate,
    ServiceRequestStatus,
    get_service_request_repository,
)

PUBLIC_VIEW_MODE = "public_view"
LOCAL_VIEW_MODE = "local"
_AUTH_COOKIE_NAME = "isite2_session"
_AUTH_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def app_mode() -> str:
    return os.getenv("ISITE2_APP_MODE", LOCAL_VIEW_MODE).strip() or LOCAL_VIEW_MODE


def is_public_view() -> bool:
    return app_mode() == PUBLIC_VIEW_MODE


def ui_surface_mode() -> str:
    configured = os.getenv("ISITE2_UI_SURFACE_MODE", "").strip()
    if configured:
        return configured
    return PUBLIC_VIEW_MODE if is_public_view() else app_mode()


def is_public_ui_surface() -> bool:
    return ui_surface_mode() == PUBLIC_VIEW_MODE


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return default


def _env_csv_set(name: str) -> set[str]:
    raw_value = os.getenv(name, "")
    return {
        item.strip().casefold()
        for item in raw_value.split(",")
        if item.strip()
    }


def runtime_features() -> dict[str, bool]:
    default_enabled = not is_public_ui_surface()
    return {
        "exports": _env_bool("ISITE2_FEATURE_EXPORTS_ENABLED", default_enabled),
        "rag": _env_bool("ISITE2_FEATURE_RAG_ENABLED", default_enabled),
        "connectors": _env_bool("ISITE2_FEATURE_CONNECTORS_ENABLED", default_enabled),
        "geocode": _env_bool("ISITE2_FEATURE_GEOCODE_ENABLED", default_enabled),
        "trafficV2": os.getenv("ISITE2_TRAFFIC_MODEL_VERSION", "v2").strip().lower()
        != "v1",
        "complaintAggregation": True,
        "ooklaPublic": _env_bool("ISITE2_PUBLIC_OOKLA_ENABLED", False),
        "ooklaPublicCountries": sorted(_env_csv_set("ISITE2_PUBLIC_OOKLA_COUNTRY_ALLOWLIST")),
    }


def _country_summary_cache_ttl_seconds() -> float:
    raw_value = os.getenv("ISITE2_COUNTRY_SUMMARY_CACHE_SECONDS", "0").strip()
    try:
        value = float(raw_value)
    except ValueError:
        return 0.0
    return max(0.0, value)


def app_auth_enabled() -> bool:
    value = os.getenv("ISITE2_APP_AUTH_ENABLED", "0").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def app_auth_user() -> str:
    return os.getenv("ISITE2_APP_AUTH_USER", "visitor").strip() or "visitor"


def app_auth_password() -> str:
    return os.getenv("ISITE2_APP_AUTH_PASSWORD", "visitor123456")


def guest_click_limit() -> int:
    raw_value = os.getenv("ISITE2_GUEST_CLICK_LIMIT", "10").strip()
    try:
        value = int(raw_value)
    except ValueError:
        return 10
    return max(1, value)


def service_requests_enabled() -> bool:
    return _env_bool("ISITE2_SERVICE_REQUESTS_ENABLED", True)


def service_request_daily_limit() -> int:
    raw_value = os.getenv("ISITE2_SERVICE_REQUEST_DAILY_LIMIT", "10").strip()
    try:
        value = int(raw_value)
    except ValueError:
        return 10
    return max(1, min(value, 100))


def _auth_session_response(username: str | None) -> dict:
    authenticated = app_auth_enabled() and username == app_auth_user()
    return {
        "enabled": app_auth_enabled(),
        "authenticated": authenticated,
        "username": username if authenticated else None,
        "guestClickLimit": guest_click_limit(),
        "usernameHint": app_auth_user(),
    }


def _auth_secret() -> bytes:
    value = os.getenv("ISITE2_APP_AUTH_SECRET")
    if value:
        return value.encode("utf-8")
    fallback = f"{app_auth_user()}:{app_auth_password()}:{PUBLIC_VIEW_MODE}"
    return hashlib.sha256(fallback.encode("utf-8")).hexdigest().encode("utf-8")


def _sign_auth_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": int(time.time()) + _AUTH_SESSION_TTL_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _authenticated_username(request: Request) -> str | None:
    if not app_auth_enabled():
        return None
    token = request.cookies.get(_AUTH_COOKIE_NAME)
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        padded_body = body + ("=" * (-len(body) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded_body.encode("ascii")))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    username = payload.get("sub")
    return username if isinstance(username, str) else None


def _auth_cookie_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return (
        request.url.scheme == "https"
        or forwarded_proto.split(",", 1)[0].strip().lower() == "https"
        or os.getenv("ENVIRONMENT", "").strip().lower() == "production"
    )


def _service_request_fingerprint(request: Request, username: str) -> str:
    session_token = request.cookies.get(_AUTH_COOKIE_NAME, "")
    client_host = request.client.host if request.client else "unknown"
    raw_value = f"{username}:{session_token}:{client_host}"
    return hmac.new(_auth_secret(), raw_value.encode("utf-8"), hashlib.sha256).hexdigest()


def _same_origin_request(request: Request) -> bool:
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return True
    origin_host = urlparse(origin).netloc.casefold()
    expected_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",", 1)[0].strip().casefold()
    return bool(origin_host and secrets.compare_digest(origin_host, expected_host))


def _admin_token_valid(request: Request) -> bool:
    configured = os.getenv("ISITE2_REQUEST_ADMIN_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    if not configured or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization.removeprefix("Bearer ").strip(), configured)


app = FastAPI(title="iSite2 API", version="0.1.0")
app.state.overlay_sync_enabled = (
    os.getenv("ISITE2_ENABLE_OVERLAY_SYNC", "1") != "0" and not is_public_view()
)
app.state.overlay_sync_checked_at = 0.0
app.state.overlay_sync_running = False
_COUNTRY_SUMMARY_CACHE_LOCK = threading.Lock()
_COUNTRY_SUMMARY_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
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
_HERO_IMAGE_TTL_SECONDS = 30 * 24 * 60 * 60
_HERO_IMAGE_FAILURE_TTL_SECONDS = 10 * 60
_HERO_IMAGE_TIMEOUT_SECONDS = 8.0
_HERO_IMAGE_MAX_BYTES = 8 * 1024 * 1024


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
    path = request.url.path
    if path == "/auth/session" and request.method in {"GET", "HEAD"}:
        return True
    if path in {"/auth/login", "/auth/logout"} and request.method == "POST":
        return True
    if path == "/service-requests" and request.method == "POST":
        return True
    if path.startswith("/admin/service-requests") and _admin_token_valid(request):
        return True

    if request.method not in {"GET", "HEAD"}:
        return False

    if path == "/":
        return True
    if path in {"/health", "/runtime-config", "/review-queue", "/discovery/status", "/updates"}:
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
    if path == "/outputs/excel/country":
        return True
    return False


@app.get("/", include_in_schema=False)
def redirect_root_to_ui():
    return RedirectResponse(url="/ui/", status_code=308)


class CreateScanRunRequest(BaseModel):
    scope: dict


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class OutputRequest(BaseModel):
    scan_run_id: UUID | None = None
    country: str | None = None
    city: str | None = None
    city_id: str | None = None
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
    locale: str | None = None


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


@app.get("/auth/session")
def auth_session(request: Request) -> dict:
    username = _authenticated_username(request)
    return _auth_session_response(username)


@app.post("/auth/login")
def auth_login(request: Request, response: Response, credentials: AuthLoginRequest) -> dict:
    if not app_auth_enabled():
        raise HTTPException(status_code=404, detail="auth disabled")
    if not (
        secrets.compare_digest(credentials.username, app_auth_user())
        and secrets.compare_digest(credentials.password, app_auth_password())
    ):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = _sign_auth_token(credentials.username)
    response.set_cookie(
        _AUTH_COOKIE_NAME,
        token,
        max_age=_AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        secure=_auth_cookie_secure(request),
        samesite="lax",
    )
    return _auth_session_response(credentials.username)


@app.post("/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    response.delete_cookie(
        _AUTH_COOKIE_NAME,
        httponly=True,
        secure=_auth_cookie_secure(request),
        samesite="lax",
    )
    return _auth_session_response(None)


@app.get("/runtime-config")
def runtime_config() -> dict:
    return {
        "mode": ui_surface_mode(),
        "features": runtime_features(),
        "localization": {
            "defaultLocale": default_locale(),
            "supportedLocales": supported_locales(),
        },
        "map": {
            "satelliteTileTemplate": "/map/satellite-tiles/{z}/{y}/{x}",
            "satelliteTileSize": _satellite_tile_size(),
            "satelliteAttribution": _satellite_tile_attribution(),
            "propertyOverlayTemplate": "/map/property-overlays/{property_id}?layer={layer}&radius_m={radius_m}",
            "footfallProvider": footfall_runtime_status(),
        },
        "auth": {
            "enabled": app_auth_enabled(),
            "guestClickLimit": guest_click_limit(),
            "usernameHint": app_auth_user(),
        },
        "serviceRequests": {
            "enabled": service_requests_enabled(),
            "dailyLimit": service_request_daily_limit(),
            "types": ["scan_enhancement", "feature_request", "ppt_report"],
            "updatesLimit": 30,
        },
    }


@app.post("/service-requests", status_code=201)
def create_service_request(
    request: Request,
    response: Response,
    payload: ServiceRequestCreate,
) -> dict:
    if not service_requests_enabled():
        raise HTTPException(status_code=404, detail="service requests disabled")
    username = _authenticated_username(request)
    if username is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not _same_origin_request(request):
        raise HTTPException(status_code=403, detail="cross-origin submission rejected")
    if isinstance(payload, ScanEnhancementRequest):
        scenes = load_scene_rules().get("scenes") or {}
        unknown_scenes = [scene for scene in payload.scene_types if scene not in scenes]
        if unknown_scenes:
            raise HTTPException(
                status_code=422,
                detail=f"unknown scene_types: {', '.join(unknown_scenes)}",
            )
    fingerprint = _service_request_fingerprint(request, username)
    ops_repository = get_service_request_repository()
    if (
        ops_repository.count_recent(fingerprint) >= service_request_daily_limit()
        and not ops_repository.has_client_request_id(payload.client_request_id)
    ):
        raise HTTPException(status_code=429, detail="daily service request limit reached")
    try:
        row, created = ops_repository.create_request(
            payload,
            username=username,
            fingerprint=fingerprint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not created:
        response.status_code = 200
    return {
        "request_id": row.id,
        "request_code": row.request_code,
        "status": row.status,
        "submitted_at": row.submitted_at.isoformat(),
        "created": created,
    }


@app.get("/updates")
def list_product_updates(
    locale: str | None = None,
    limit: int = Query(default=30, ge=1, le=50),
) -> dict:
    resolved_locale = resolve_locale(locale)
    updates = get_service_request_repository().list_updates(resolved_locale, limit=limit)
    return {"locale": resolved_locale, "count": len(updates), "updates": updates}


def _require_admin(request: Request) -> None:
    if not _admin_token_valid(request):
        raise HTTPException(status_code=401, detail="invalid admin token")


def _admin_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@app.get("/admin/service-requests", include_in_schema=False)
def admin_list_service_requests(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    _require_admin(request)
    statuses = [item.strip() for item in (status or "").split(",") if item.strip()]
    rows = get_service_request_repository().list_requests(statuses=statuses or None, limit=limit)
    return {"count": len(rows), "requests": rows}


@app.get("/admin/service-requests/{request_code}", include_in_schema=False)
def admin_get_service_request(request_code: str, request: Request) -> dict:
    _require_admin(request)
    row = get_service_request_repository().get_request(request_code, include_email=True)
    if row is None:
        raise HTTPException(status_code=404, detail="service request not found")
    return row


def _admin_transition(
    request_code: str,
    target: ServiceRequestStatus,
    decision: RequestDecision,
) -> dict:
    try:
        return get_service_request_repository().transition(
            request_code,
            target,
            actor=decision.actor,
            note=decision.note,
        )
    except (LookupError, ValueError) as exc:
        raise _admin_error(exc) from exc


@app.post("/admin/service-requests/{request_code}/approve", include_in_schema=False)
def admin_approve_service_request(
    request_code: str, request: Request, decision: RequestDecision
) -> dict:
    _require_admin(request)
    return _admin_transition(request_code, ServiceRequestStatus.APPROVED, decision)


@app.post("/admin/service-requests/{request_code}/reject", include_in_schema=False)
def admin_reject_service_request(
    request_code: str, request: Request, decision: RequestDecision
) -> dict:
    _require_admin(request)
    return _admin_transition(request_code, ServiceRequestStatus.REJECTED, decision)


@app.post("/admin/service-requests/{request_code}/start", include_in_schema=False)
def admin_start_service_request(
    request_code: str, request: Request, decision: RequestDecision
) -> dict:
    _require_admin(request)
    return _admin_transition(request_code, ServiceRequestStatus.IN_PROGRESS, decision)


@app.post("/admin/service-requests/{request_code}/block", include_in_schema=False)
def admin_block_service_request(
    request_code: str, request: Request, blocked: RequestBlock
) -> dict:
    _require_admin(request)
    return _admin_transition(
        request_code,
        ServiceRequestStatus.BLOCKED,
        RequestDecision(actor=blocked.actor, note=blocked.reason),
    )


@app.post("/admin/service-requests/{request_code}/prepare-completion", include_in_schema=False)
def admin_prepare_service_request_completion(
    request_code: str, request: Request, completion: CompletionPrepare
) -> dict:
    _require_admin(request)
    try:
        return get_service_request_repository().prepare_completion(request_code, completion)
    except (LookupError, ValueError) as exc:
        raise _admin_error(exc) from exc


@app.get("/admin/service-requests/{request_code}/delivery", include_in_schema=False)
def admin_get_service_request_delivery(request_code: str, request: Request) -> dict:
    _require_admin(request)
    try:
        return get_service_request_repository().delivery_payload(request_code)
    except (LookupError, ValueError) as exc:
        raise _admin_error(exc) from exc


@app.post("/admin/service-requests/{request_code}/delivery-result", include_in_schema=False)
def admin_record_service_request_delivery(
    request_code: str, request: Request, result: DeliveryResult
) -> dict:
    _require_admin(request)
    try:
        return get_service_request_repository().record_delivery(request_code, result)
    except (LookupError, ValueError) as exc:
        raise _admin_error(exc) from exc


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


@app.get("/rules/localization")
def get_localization(locale: str | None = None) -> dict:
    return localization_payload(locale)


@app.get("/properties")
def list_properties(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    city_id: str | None = None,
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
    locale: str | None = None,
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        city_id=city_id,
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
    include_blocked = include_blocked_quality or candidate_quality_status is not None
    resolved_locale = resolve_locale(locale)
    if is_public_view():
        public_page_loader = getattr(repository, "list_public_property_page", None)
        if callable(public_page_loader):
            page = public_page_loader(
                filters,
                locale=resolved_locale,
                include_blocked_quality=include_blocked,
                limit=top_n,
            )
            if page is not None:
                return jsonable_encoder(page)
    if top_n is not None:
        page_loader = getattr(repository, "list_property_page", None)
    else:
        page_loader = None
    if callable(page_loader):
        page = page_loader(
            filters,
            surface="main_table",
            include_blocked_quality=include_blocked,
            limit=top_n,
        )
        packets = page["packets"]
        candidate_count = page["candidate_count"]
        visible_packets = packets
    else:
        packets = repository.list_properties(filters)
        packets = filter_packets_for_surface(
            packets,
            "main_table",
            include_blocked_quality=include_blocked,
        )
        visible_packets = display_slice(packets, top_n)
        candidate_count = len(packets)
    return jsonable_encoder(
        {
            "candidate_count": candidate_count,
            "display_count": len(visible_packets),
            "locale": resolved_locale,
            "packets": _localized_packets(visible_packets, locale),
        }
    )


@app.get("/properties/search")
def search_properties(
    q: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    _ensure_overlay_registry_synced()
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="property search query cannot be blank")
    if is_public_view():
        public_search = getattr(repository, "search_public_properties", None)
        if callable(public_search):
            results = public_search(query, limit=limit)
            return jsonable_encoder(
                {"query": query, "count": len(results), "results": results}
            )
    search = getattr(repository, "search_properties", None)
    results = search(query, limit=limit) if callable(search) else []
    return jsonable_encoder({"query": query, "count": len(results), "results": results})


@app.get("/properties/{property_id}")
def get_property(property_id: UUID, locale: str | None = None) -> dict:
    _ensure_overlay_registry_synced()
    if is_public_view():
        public_loader = getattr(repository, "get_public_property_packet", None)
        if callable(public_loader):
            payload = public_loader(str(property_id), locale=resolve_locale(locale))
            if payload is not None:
                return jsonable_encoder(payload)
    packet = repository.get_property(property_id)
    if packet is None:
        raise HTTPException(status_code=404, detail="property not found")
    return jsonable_encoder(_localized_packet_payload(packet, locale))


@app.get("/review-queue")
def list_review_queue(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    city_id: str | None = None,
    scene_type: str | None = None,
    evidence_status: str | None = None,
    value_class: str | None = None,
    action_class: str | None = None,
    indoor_system_presence: str | None = None,
    status: str | None = None,
    locale: str | None = None,
) -> list[dict]:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        city_id=city_id,
        scene_type=scene_type,
        evidence_status=evidence_status,
        value_class=value_class,
        action_class=action_class,
        indoor_system_presence=indoor_system_presence,
        candidate_quality_status=None,
    )
    resolved_locale = resolve_locale(locale)
    if is_public_view():
        public_loader = getattr(repository, "list_public_review_queue_rows", None)
        if callable(public_loader):
            public_rows = public_loader(filters, locale=resolved_locale, status=status)
            if public_rows is not None:
                return jsonable_encoder(public_rows)
    rows = []
    localization_cache = _localization_cache()
    review_loader = getattr(repository, "list_review_queue_rows", None)
    if callable(review_loader):
        review_rows = review_loader(filters, status=status)
        for review in review_rows:
            rows.append(
                {
                    "property_id": review["property_id"],
                    "property_name": review["property_name"],
                    "country": review["country"],
                    "city": review["city"],
                    "scene_type": review["scene_type"],
                    "candidate_quality_status": review["candidate_quality_status"],
                    "visibility": review["visibility"],
                    "quality_issues": review["quality_issues"],
                    "reason": review["reason"],
                    "next_action": review["next_action"],
                    "status": review["status"],
                    "review_type": review["review_type"],
                    "severity": review["severity"],
                    "gate_name": review["gate_name"],
                    "field_path": review["field_path"],
                    "blocking_surfaces": review["blocking_surfaces"],
                    "source_url": review["source_url"],
                    "suggested_query": review["suggested_query"],
                    "localized": {
                        "locale": resolve_locale(locale),
                        "scene_label": scene_label(review["scene_type"], locale),
                        "reason": _localized_review_text(
                            review["reason"],
                            "review.reason",
                            locale,
                            localization_cache,
                        ),
                        "reason_original": review["reason"],
                        "next_action": _localized_review_text(
                            review["next_action"],
                            "review.next_action",
                            locale,
                            localization_cache,
                        ),
                        "next_action_original": review["next_action"],
                        "status_label": enum_label(review["status"], locale),
                    },
                }
            )
    else:
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
                        "localized": {
                            "locale": resolve_locale(locale),
                            "scene_label": scene_label(packet.entity.scene_type, locale),
                            "reason": _localized_review_text(
                                review.reason,
                                "review.reason",
                                locale,
                                localization_cache,
                            ),
                            "reason_original": review.reason,
                            "next_action": _localized_review_text(
                                review.next_action,
                                "review.next_action",
                                locale,
                                localization_cache,
                            ),
                            "next_action_original": review.next_action,
                            "status_label": enum_label(review.status, locale),
                        },
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
    city_id: str | None = None,
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
    locale: str | None = None,
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        city_id=city_id,
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
    include_blocked = include_blocked_quality or candidate_quality_status is not None
    map_loader = getattr(repository, "list_map_property_rows", None)
    resolved_locale = resolve_locale(locale)
    if is_public_view():
        public_loader = getattr(repository, "list_public_map_features", None)
        if callable(public_loader):
            public_features = public_loader(
                filters,
                locale=resolved_locale,
                include_blocked_quality=include_blocked,
            )
            if public_features is not None:
                return jsonable_encoder({"type": "FeatureCollection", "features": public_features})
    if callable(map_loader):
        for row in map_loader(filters, include_blocked_quality=include_blocked):
            hero_image = row.get("hero_image") or {}
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [row["longitude"], row["latitude"]],
                    },
                    "properties": {
                        "property_id": str(row["property_id"]),
                        "property_name": row["property_name"],
                        "country": row["country"],
                        "city": row["city"],
                        "city_id": row.get("city_id"),
                        "city_assignment": row.get("city_assignment"),
                        "scene_type": row["scene_type"],
                        "evidence_status": row["evidence_status"],
                        "value_class": row["value_class"],
                        "action_class": row["action_class"],
                        "recommended_solution": row["recommended_solution"],
                        "main_metric_text": row["main_metric_text"],
                        "annual_visits_est": row["annual_visits_est"],
                        "busy_hour_traffic_gb": row["busy_hour_traffic_gb"],
                        "review_count": row["review_count"],
                        "source_count": row["source_count"],
                        "indoor_system_presence": row["indoor_system_presence"],
                        "indoor_rat": row["indoor_rat"],
                        "proxy_level": row["proxy_level"],
                        "last_scan_at": last_scan_at,
                        "google_maps_link": row["google_maps_link"],
                        "geocode_precision": row["geocode_precision"],
                        "map_source": row["map_source"],
                        "coordinate_status": row["coordinate_status"],
                        "candidate_quality_status": row["candidate_quality_status"],
                        "visibility": row["visibility"],
                        "quality_issues": row["quality_issues"],
                        "hero_image_url": hero_image.get("url"),
                        "hero_image_alt": hero_image.get("alt_text"),
                        "hero_image_source_name": hero_image.get("source_name"),
                        "localized": _map_feature_localized_labels(row, resolved_locale),
                    },
                }
            )
    else:
        packets = repository.list_properties(filters)
        packets = filter_packets_for_surface(
            packets,
            "map",
            include_blocked_quality=include_blocked,
        )
        for packet in packets:
            entity = packet.entity
            if entity.longitude is None or entity.latitude is None:
                continue
            if not is_map_ready_coordinate(entity.coordinate_status):
                continue
            hero_image = entity.hero_image
            row = {
                "scene_type": entity.scene_type,
                "evidence_status": packet.conclusion.evidence_status,
                "value_class": packet.conclusion.value_class,
                "action_class": packet.conclusion.action_class,
                "recommended_solution": packet.conclusion.recommended_solution,
                "indoor_system_presence": packet.build_status.indoor_system_presence,
                "indoor_rat": packet.build_status.indoor_rat,
                "proxy_level": packet.scene.proxy_level,
            }
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
                        "city_id": (
                            entity.city_assignment.city_id
                            if entity.city_assignment is not None
                            else None
                        ),
                        "city_assignment": (
                            entity.city_assignment.model_dump(mode="json")
                            if entity.city_assignment is not None
                            else None
                        ),
                        "scene_type": entity.scene_type,
                        "evidence_status": packet.conclusion.evidence_status,
                        "value_class": packet.conclusion.value_class,
                        "action_class": packet.conclusion.action_class,
                        "recommended_solution": packet.conclusion.recommended_solution,
                        "main_metric_text": (
                            packet.evidence[0].field_value if packet.evidence else ""
                        ),
                        "annual_visits_est": packet.scene.annual_visits_est,
                        "busy_hour_traffic_gb": (
                            packet.demand.busy_hour_traffic_gb if packet.demand else None
                        ),
                        "review_count": len(packet.review_queue),
                        "source_count": _packet_evidence_unit_count(packet),
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
                        "localized": _map_feature_localized_labels(row, resolved_locale),
                    },
                }
            )
    return jsonable_encoder({"type": "FeatureCollection", "features": features})


@app.get("/map/country-summary")
def country_summary(scan_run_id: UUID | None = None, locale: str | None = None) -> list[dict]:
    resolved_locale = resolve_locale(locale)
    cache_ttl = _country_summary_cache_ttl_seconds()
    cache_key: tuple[str, str] | None = None
    if scan_run_id is None and cache_ttl > 0:
        cache_key = (app_mode(), resolved_locale)
        now = time.monotonic()
        with _COUNTRY_SUMMARY_CACHE_LOCK:
            cached = _COUNTRY_SUMMARY_CACHE.get(cache_key)
            if cached is not None and now - cached[0] <= cache_ttl:
                return jsonable_encoder(deepcopy(cached[1]))
    if is_public_view():
        public_loader = getattr(repository, "list_public_country_summaries", None)
        if callable(public_loader):
            rows = public_loader(locale=resolved_locale, scan_run_id=scan_run_id)
            if rows is not None:
                payload = jsonable_encoder(_country_summaries_with_scan_maturity(rows))
                if cache_key is not None:
                    with _COUNTRY_SUMMARY_CACHE_LOCK:
                        _COUNTRY_SUMMARY_CACHE[cache_key] = (time.monotonic(), deepcopy(payload))
                return payload
    payload = jsonable_encoder(
        _localized_summary_rows(
            _country_summaries_with_scan_maturity(
                _country_summary_rows(scan_run_id=scan_run_id)
            ),
            resolved_locale,
        )
    )
    if cache_key is not None:
        with _COUNTRY_SUMMARY_CACHE_LOCK:
            _COUNTRY_SUMMARY_CACHE[cache_key] = (time.monotonic(), deepcopy(payload))
    return payload


def _country_summaries_with_scan_maturity(rows: list[dict]) -> list[dict]:
    progress_loader = getattr(repository, "list_discovery_progress", None)
    progress_rows = (
        progress_loader(countries=[str(row.get("country") or "") for row in rows])
        if callable(progress_loader)
        else []
    )
    return add_scan_maturity_to_country_summaries(rows, progress_rows)


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


@app.get("/map/property-overlays/{property_id}")
def property_overlay(
    property_id: UUID,
    layer: str = Query(..., pattern="^(mobile_network|footfall)$"),
    radius_m: int = Query(5000, ge=100, le=5000),
) -> dict:
    _ensure_overlay_registry_synced()
    target = _property_overlay_target(property_id)
    if target is None:
        raise HTTPException(status_code=404, detail="property not found")
    if target["latitude"] is None or target["longitude"] is None:
        return _empty_overlay(layer, "no_coordinate", "Property coordinate is unavailable.")

    if layer == "mobile_network":
        return _mobile_network_overlay(
            country=str(target["country"]),
            latitude=float(target["latitude"]),
            longitude=float(target["longitude"]),
            radius_m=radius_m,
        )

    result = cached_footfall_overlay(
        property_id=str(property_id),
        property_name=str(target["property_name"]),
        country=str(target["country"]),
        latitude=float(target["latitude"]),
        longitude=float(target["longitude"]),
        radius_m=radius_m,
    )
    return footfall_cells_to_feature_collection(result)


@app.api_route("/map/hero-image", methods=["GET", "HEAD"])
def hero_image_proxy(url: str = Query(..., min_length=8)) -> Response:
    upstream_url = stable_hero_image_url(url)
    _validate_public_image_url(upstream_url)
    content, content_type, final_url = _hero_image_from_cache_or_upstream(
        upstream_url,
        hero_image_url_variants(url),
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=604800",
            "X-iSite2-Hero-Image-Source": "cache-or-upstream",
            "X-iSite2-Hero-Image-Upstream-URL": final_url,
        },
    )


@app.get("/map/city-summary")
def city_summary(
    scan_run_id: UUID | None = None,
    country: str | None = None,
    city: str | None = None,
    city_id: str | None = None,
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
    locale: str | None = None,
) -> dict:
    _ensure_overlay_registry_synced()
    filters = _property_filters(
        country=country,
        scan_run_id=scan_run_id,
        city=city,
        city_id=city_id,
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
    if is_public_view():
        public_loader = getattr(repository, "list_public_city_summaries", None)
        if callable(public_loader):
            rows = public_loader(
                filters,
                locale=resolve_locale(locale),
                include_blocked_quality=include_blocked,
            )
            if rows is not None:
                return jsonable_encoder({"cities": rows})
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
    return jsonable_encoder({"cities": _localized_summary_rows(rows, locale)})


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
        row["source_count"] += _packet_evidence_unit_count(packet)
        row["scenes"][packet.entity.scene_type] = row["scenes"].get(packet.entity.scene_type, 0) + 1
    rows = []
    for row in summary.values():
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
        row["source_count"] += _packet_evidence_unit_count(packet)
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
        locale=request.locale,
        localization_cache=_localization_cache(),
    )
    artifact = _output_artifact(request, "excel", path, artifact_base)
    return jsonable_encoder(artifact)


@app.get("/outputs/excel/country")
def download_country_excel(
    country: str = Query(..., min_length=1, max_length=128),
    locale: str | None = None,
) -> FileResponse:
    resolved_country = country.strip()
    if not resolved_country:
        raise HTTPException(status_code=422, detail="country cannot be blank")
    resolved_locale = resolve_locale(locale)
    state_hash_loader = getattr(repository, "country_export_state_hash", None)
    if not callable(state_hash_loader):
        raise HTTPException(status_code=409, detail="country report state is unavailable")
    try:
        path, filename = resolve_country_excel_report(
            country=resolved_country,
            locale=resolved_locale,
            expected_state_hash=state_hash_loader(
                resolved_country,
                public=is_public_view(),
            ),
        )
    except CountryReportUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Cache-Control": "no-store"},
    )


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
        locale=request.locale,
        localization_cache=_localization_cache(),
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


class _HeroImageUpstreamError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _validate_public_image_url(url: str) -> None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="hero image url must be public HTTP(S)")
    host = (parsed.hostname or "").strip().casefold()
    if not host:
        raise HTTPException(status_code=400, detail="hero image url host missing")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="private hero image hosts are blocked")
    try:
        address = ip_address(host)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise HTTPException(status_code=400, detail="private hero image hosts are blocked")


def _hero_image_cache_dir() -> Path:
    return Path(
        os.getenv(
            "ISITE2_HERO_IMAGE_CACHE_DIR",
            str(_PROJECT_ROOT / ".tmp" / "isite2_hero_image_cache"),
        )
    )


def _hero_image_cache_paths(url: str) -> tuple[Path, Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    base = _hero_image_cache_dir() / key[:2] / key[2:4]
    return base / f"{key}.image", base / f"{key}.json", base / f"{key}.fail"


def _hero_image_from_cache_or_upstream(
    url: str,
    fallback_urls: list[str] | None = None,
) -> tuple[bytes, str, str]:
    image_path, meta_path, fail_path = _hero_image_cache_paths(url)
    now = time.time()
    if image_path.exists() and now - image_path.stat().st_mtime <= _HERO_IMAGE_TTL_SECONDS:
        return (
            image_path.read_bytes(),
            _hero_image_content_type(meta_path),
            _hero_image_final_url(meta_path, url),
        )
    urls_to_try = _dedupe_hero_image_urls(fallback_urls or [url])
    if (
        len(urls_to_try) == 1
        and fail_path.exists()
        and now - fail_path.stat().st_mtime <= _HERO_IMAGE_FAILURE_TTL_SECONDS
    ):
        raise HTTPException(status_code=502, detail="hero image upstream recently failed")
    try:
        content, content_type, final_url = _fetch_hero_image_from_upstream_variants(urls_to_try)
    except Exception as exc:  # noqa: BLE001 - turn network/image failures into stable UI fallback
        if (
            not (isinstance(exc, _HeroImageUpstreamError) and exc.status_code == 429)
            and len(urls_to_try) == 1
        ):
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(str(exc), encoding="utf-8")
        raise HTTPException(status_code=502, detail="hero image upstream failed") from exc

    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    meta_path.write_text(
        json.dumps({"content_type": content_type, "final_url": final_url}),
        encoding="utf-8",
    )
    if fail_path.exists():
        fail_path.unlink()
    return content, content_type, final_url


def _hero_image_content_type(meta_path: Path) -> str:
    if not meta_path.exists():
        return "image/jpeg"
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "image/jpeg"
    content_type = str(payload.get("content_type") or "").strip()
    return content_type or "image/jpeg"


def _hero_image_final_url(meta_path: Path, default_url: str) -> str:
    if not meta_path.exists():
        return default_url
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_url
    final_url = str(payload.get("final_url") or "").strip()
    return final_url or default_url


def _fetch_hero_image_from_upstream_variants(
    urls: list[str],
) -> tuple[bytes, str, str]:
    last_error: Exception | None = None
    for url in urls:
        try:
            _validate_public_image_url(url)
            return _fetch_hero_image_from_upstream(url)
        except Exception as exc:  # noqa: BLE001 - try the next stable variant
            last_error = exc
    if last_error is not None:
        raise last_error
    raise _HeroImageUpstreamError("no hero image upstream urls")


def _dedupe_hero_image_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        value = str(url or "").strip()
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _fetch_hero_image_from_upstream(url: str) -> tuple[bytes, str, str]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": "iSite2/0.1 hero-image-cache",
    }
    with httpx.Client(follow_redirects=True, timeout=_HERO_IMAGE_TIMEOUT_SECONDS) as client:
        response = client.get(url, headers=headers)
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if response.status_code != 200 or not response.content or not content_type.startswith("image/"):
        raise _HeroImageUpstreamError(
            f"upstream image failed with status={response.status_code} "
            f"content_type={content_type or 'unknown'}",
            status_code=response.status_code,
        )
    if len(response.content) > _HERO_IMAGE_MAX_BYTES:
        raise _HeroImageUpstreamError("upstream image exceeds max cache size")
    return response.content, content_type, str(response.url)


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

    if (
        fail_path.exists()
        and now - fail_path.stat().st_mtime <= _SATELLITE_TILE_FAILURE_TTL_SECONDS
    ):
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


def _empty_overlay(layer: str, status: str, message: str) -> dict:
    return {
        "type": "FeatureCollection",
        "layer": layer,
        "provider": "isite2",
        "status": status,
        "message": message,
        "features": [],
    }


def _property_overlay_target(property_id: UUID) -> dict | None:
    with repository.session_factory() as session:
        row = session.scalar(select(PropertyDB).where(PropertyDB.id == str(property_id)))
        if row is None:
            return None
        return {
            "property_name": row.canonical_name,
            "country": row.country,
            "latitude": row.latitude,
            "longitude": row.longitude,
        }


def _nearby_mobile_network_observations(
    *,
    country: str,
    latitude: float,
    longitude: float,
    radius_m: int,
) -> list[tuple[float, NetworkPerformanceObservationDB, PropertyNetworkPerformanceRollupDB]]:
    with repository.session_factory() as session:
        rows = session.execute(
            select(NetworkPerformanceObservationDB, PropertyNetworkPerformanceRollupDB)
            .join(
                PropertyNetworkPerformanceRollupDB,
                PropertyNetworkPerformanceRollupDB.observation_id == NetworkPerformanceObservationDB.id,
            )
            .where(
                NetworkPerformanceObservationDB.country == country,
                NetworkPerformanceObservationDB.service_type == "mobile",
                PropertyNetworkPerformanceRollupDB.service_type == "mobile",
            )
            .order_by(
                PropertyNetworkPerformanceRollupDB.calculated_at.desc(),
                NetworkPerformanceObservationDB.tests.desc(),
            )
        ).all()

    by_quadkey: dict[str, tuple[float, NetworkPerformanceObservationDB, PropertyNetworkPerformanceRollupDB]] = {}
    for observation, rollup in rows:
        distance_m = _distance_m(
            latitude,
            longitude,
            float(observation.tile_latitude),
            float(observation.tile_longitude),
        )
        if distance_m > radius_m:
            continue
        existing = by_quadkey.get(observation.quadkey)
        candidate_rank = (distance_m, -int(observation.tests or 0), -int(observation.devices or 0))
        if existing is None:
            by_quadkey[observation.quadkey] = (distance_m, observation, rollup)
            continue
        existing_distance, existing_observation, _ = existing
        existing_rank = (
            existing_distance,
            -int(existing_observation.tests or 0),
            -int(existing_observation.devices or 0),
        )
        if candidate_rank < existing_rank:
            by_quadkey[observation.quadkey] = (distance_m, observation, rollup)
    return sorted(by_quadkey.values(), key=lambda row: row[0])


def _nearby_mobile_network_tiles(
    *,
    country: str,
    latitude: float,
    longitude: float,
    radius_m: int,
) -> list[tuple[float, NetworkPerformanceTileDB]]:
    with repository.session_factory() as session:
        rows = session.scalars(
            select(NetworkPerformanceTileDB)
            .where(
                NetworkPerformanceTileDB.country == country,
                NetworkPerformanceTileDB.service_type == "mobile",
            )
            .order_by(
                NetworkPerformanceTileDB.period.desc(),
                NetworkPerformanceTileDB.tests.desc(),
            )
        ).all()

    by_quadkey: dict[str, tuple[float, NetworkPerformanceTileDB]] = {}
    for tile in rows:
        distance_m = _distance_m(
            latitude,
            longitude,
            float(tile.tile_latitude),
            float(tile.tile_longitude),
        )
        if distance_m > radius_m:
            continue
        existing = by_quadkey.get(tile.quadkey)
        candidate_rank = (distance_m, -int(tile.tests or 0), -int(tile.devices or 0))
        if existing is None:
            by_quadkey[tile.quadkey] = (distance_m, tile)
            continue
        existing_distance, existing_tile = existing
        existing_rank = (
            existing_distance,
            -int(existing_tile.tests or 0),
            -int(existing_tile.devices or 0),
        )
        if candidate_rank < existing_rank:
            by_quadkey[tile.quadkey] = (distance_m, tile)
    return sorted(by_quadkey.values(), key=lambda row: row[0])


def _mobile_network_overlay(
    *,
    country: str,
    latitude: float,
    longitude: float,
    radius_m: int,
) -> dict:
    public_countries = _env_csv_set("ISITE2_PUBLIC_OOKLA_COUNTRY_ALLOWLIST")
    if is_public_ui_surface() and not (
        runtime_features()["ooklaPublic"]
        or country.casefold() in public_countries
    ):
        return _empty_overlay(
            "mobile_network",
            "disabled",
            "Ookla public mobile overlay is disabled for this country.",
        )
    tile_rows = _nearby_mobile_network_tiles(
        country=country,
        latitude=latitude,
        longitude=longitude,
        radius_m=radius_m,
    )
    if tile_rows:
        return _mobile_network_tile_overlay(tile_rows, radius_m)

    observations = _nearby_mobile_network_observations(
        country=country,
        latitude=latitude,
        longitude=longitude,
        radius_m=radius_m,
    )
    if not observations:
        return _empty_overlay(
            "mobile_network",
            "no_data",
            f"No Ookla mobile tile is available within {_format_radius_km(radius_m)} of this property.",
        )
    features: list[dict] = []
    for distance_m, observation, rollup in observations:
        properties = _mobile_network_tile_properties(observation, rollup, distance_m)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_quadkey_polygon(observation.quadkey)],
                },
                "properties": {
                    **properties,
                    "feature_kind": "tile",
                },
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [observation.tile_longitude, observation.tile_latitude],
                },
                "properties": {
                    **properties,
                    "feature_kind": "tile_center",
                    "center_label": "Tile center",
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "layer": "mobile_network",
        "provider": "ookla_open_data",
        "status": "ready",
        "message": f"{len(observations)} Ookla mobile network tiles within {_format_radius_km(radius_m)}.",
        "proxy_note": (
            f"Ookla mobile z16 tiles within {_format_radius_km(radius_m)}; "
            "not indoor DAS/build evidence."
        ),
        "features": features,
    }


def _mobile_network_tile_overlay(
    tile_rows: list[tuple[float, NetworkPerformanceTileDB]],
    radius_m: int,
) -> dict:
    features: list[dict] = []
    for distance_m, tile in tile_rows:
        properties = _mobile_network_tile_table_properties(tile, distance_m)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_quadkey_polygon(tile.quadkey)],
                },
                "properties": {
                    **properties,
                    "feature_kind": "tile",
                },
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [tile.tile_longitude, tile.tile_latitude],
                },
                "properties": {
                    **properties,
                    "feature_kind": "tile_center",
                    "center_label": "Tile center",
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "layer": "mobile_network",
        "provider": "ookla_open_data",
        "status": "ready",
        "message": f"{len(tile_rows)} Ookla mobile network tiles within {_format_radius_km(radius_m)}.",
        "proxy_note": (
            f"Ookla mobile z16 tiles within {_format_radius_km(radius_m)}; "
            "not indoor DAS/build evidence."
        ),
        "features": features,
    }


def _mobile_network_tile_table_properties(
    tile: NetworkPerformanceTileDB,
    distance_m: float,
) -> dict:
    confidence_opacity = {"high": 1.0, "medium": 0.72, "low": 0.46}.get(
        str(tile.confidence).casefold(),
        0.5,
    )
    return {
        "cell_id": tile.quadkey,
        "score": round(
            _mobile_network_score(
                download_mbps=tile.avg_download_mbps,
                latency_ms=tile.avg_loaded_latency_down_ms or tile.avg_latency_ms,
                performance_class=tile.performance_class,
            ),
            4,
        ),
        "opacity": confidence_opacity,
        "metric_value": tile.avg_download_mbps,
        "metric_label": "Mobile download Mbps",
        "download_mbps": tile.avg_download_mbps,
        "upload_mbps": tile.avg_upload_mbps,
        "latency_ms": tile.avg_latency_ms,
        "loaded_latency_down_ms": tile.avg_loaded_latency_down_ms,
        "tests": tile.tests,
        "devices": tile.devices,
        "confidence": tile.confidence,
        "performance_class": tile.performance_class,
        "source_name": "Ookla Open Data",
        "source_url": tile.source_url,
        "period": tile.period,
        "resolution_m": 610.8,
        "license": tile.license,
        "tile_center_latitude": tile.tile_latitude,
        "tile_center_longitude": tile.tile_longitude,
        "distance_to_property_m": round(distance_m, 1),
        "tile_scope": "radius_tile",
    }


def _mobile_network_tile_properties(
    observation: NetworkPerformanceObservationDB,
    rollup: PropertyNetworkPerformanceRollupDB,
    distance_m: float,
) -> dict:
    confidence_opacity = {"high": 1.0, "medium": 0.72, "low": 0.46}.get(
        str(rollup.confidence).casefold(),
        0.5,
    )
    return {
        "cell_id": observation.quadkey,
        "score": round(
            _mobile_network_score(
                download_mbps=observation.avg_download_mbps,
                latency_ms=observation.avg_loaded_latency_down_ms
                or observation.avg_latency_ms,
                performance_class=rollup.performance_class,
            ),
            4,
        ),
        "opacity": confidence_opacity,
        "metric_value": observation.avg_download_mbps,
        "metric_label": "Mobile download Mbps",
        "download_mbps": observation.avg_download_mbps,
        "upload_mbps": observation.avg_upload_mbps,
        "latency_ms": observation.avg_latency_ms,
        "loaded_latency_down_ms": observation.avg_loaded_latency_down_ms,
        "tests": observation.tests,
        "devices": observation.devices,
        "confidence": rollup.confidence,
        "performance_class": rollup.performance_class,
        "source_name": "Ookla Open Data",
        "source_url": observation.source_url,
        "period": observation.period,
        "resolution_m": 610.8,
        "license": observation.license,
        "tile_center_latitude": observation.tile_latitude,
        "tile_center_longitude": observation.tile_longitude,
        "distance_to_property_m": round(distance_m, 1),
    }


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _format_radius_km(radius_m: int) -> str:
    radius_km = radius_m / 1000
    return f"{radius_km:g} km"


def _mobile_network_score(
    *,
    download_mbps: float,
    latency_ms: float | None,
    performance_class: str,
) -> float:
    class_floor = {"poor": 0.2, "moderate": 0.52, "good": 0.78}.get(
        str(performance_class).casefold(),
        0.45,
    )
    download_score = min(1.0, max(0.0, download_mbps / 120.0))
    latency_score = 0.5
    if latency_ms is not None:
        latency_score = 1.0 - min(1.0, max(0.0, (latency_ms - 40.0) / 360.0))
    return max(0.0, min(1.0, class_floor * 0.45 + download_score * 0.4 + latency_score * 0.15))


def _quadkey_polygon(quadkey: str) -> list[list[float]]:
    tile_x, tile_y, zoom = quadkey_to_tile_xy(quadkey)
    west, north = _tile_xy_to_lon_lat(tile_x, tile_y, zoom)
    east, south = _tile_xy_to_lon_lat(tile_x + 1, tile_y + 1, zoom)
    return [
        [west, north],
        [east, north],
        [east, south],
        [west, south],
        [west, north],
    ]


def _tile_xy_to_lon_lat(tile_x: int, tile_y: int, zoom: int) -> tuple[float, float]:
    size = 1 << zoom
    lon = tile_x / size * 360.0 - 180.0
    mercator = math.pi * (1 - 2 * tile_y / size)
    lat = math.degrees(math.atan(math.sinh(mercator)))
    return lon, lat


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
            f"upstream tile failed with status={response.status_code} "
            f"content_type={content_type or 'unknown'}",
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
            _SATELLITE_TILE_BREAKER["opened_until"] = (
                time.time() + _SATELLITE_TILE_BREAKER_TTL_SECONDS
            )
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


def _localization_cache() -> DatabaseLocalizationCache | None:
    engine = getattr(repository, "engine", None)
    return DatabaseLocalizationCache(engine) if engine is not None else None


def _localized_packets(packets: list, locale: str | None = None) -> list[dict]:
    cache = _localization_cache()
    return [_localized_packet_payload(packet, locale, cache) for packet in packets]


def _localized_packet_payload(
    packet,
    locale: str | None = None,
    cache: DatabaseLocalizationCache | None = None,
) -> dict:
    payload = jsonable_encoder(packet)
    payload["localized"] = localize_packet(
        packet,
        locale=locale,
        cache=cache if cache is not None else _localization_cache(),
        allow_provider=False,
    )
    return payload


def _localized_review_text(
    text: str,
    text_kind: str,
    locale: str | None,
    cache: DatabaseLocalizationCache | None = None,
) -> str:
    active_cache = cache if cache is not None else _localization_cache()
    if active_cache is None:
        return text
    localized = localize_packet_free_text(text, text_kind, locale, active_cache)
    return localized


def localize_packet_free_text(
    source_text: str,
    text_kind: str,
    locale: str | None,
    cache: DatabaseLocalizationCache,
) -> str:
    from isite2.localization import localize_text

    return localize_text(
        source_text,
        text_kind=text_kind,
        target_locale=locale,
        cache=cache,
        allow_provider=False,
        fallback_text=generic_free_text_fallback(text_kind, locale),
    ).translated_text


def _map_feature_localized_labels(row: dict, resolved_locale: str) -> dict:
    return {
        "locale": resolved_locale,
        "scene_label": scene_label(row["scene_type"], resolved_locale),
        "evidence_status_label": enum_label(row["evidence_status"], resolved_locale),
        "value_class_label": enum_label(row["value_class"], resolved_locale),
        "action_class_label": enum_label(row["action_class"], resolved_locale),
        "recommended_solution_label": enum_label(
            row["recommended_solution"],
            resolved_locale,
        ),
        "indoor_system_presence_label": enum_label(
            row["indoor_system_presence"],
            resolved_locale,
        ),
        "indoor_rat_label": enum_label(row["indoor_rat"], resolved_locale),
        "proxy_level_label": enum_label(row["proxy_level"], resolved_locale),
    }


def _packet_evidence_unit_count(packet) -> int:
    return max(1, len(getattr(packet, "evidence", []) or []))


def _localized_summary_rows(rows: list[dict], locale: str | None = None) -> list[dict]:
    resolved_locale = resolve_locale(locale)
    localized_rows = []
    for row in rows:
        payload = dict(row)
        scenes = payload.get("scenes") or {}
        payload["localized"] = {
            "locale": resolved_locale,
            "scenes": {scene: scene_label(scene, resolved_locale) for scene in scenes},
        }
        localized_rows.append(payload)
    return localized_rows


def _output_payload(
    request: OutputRequest,
    artifact_type: str,
    suffix: str,
) -> tuple[list, Path, dict]:
    resolved_locale = resolve_locale(request.locale)
    filters = _property_filters(
        country=request.country,
        city=request.city,
        city_id=request.city_id,
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
            packet for packet in result.packets if _packet_matches_output_filters(packet, filters)
        ]
        packets = filter_packets_for_surface(
            packets,
            "export",
            include_blocked_quality=request.include_blocked_quality
            or request.candidate_quality_status is not None,
        )
        return (
            packets,
            _output_path(request.scan_run_id, suffix),
            {
                "candidate_count": len(packets),
                "filter_snapshot": {
                    "scan_run_id": str(request.scan_run_id),
                    **filters,
                    "locale": resolved_locale,
                    "evidence_text_mode": "source_and_translation",
                },
            },
        )

    packets = repository.list_properties(filters)
    packets = filter_packets_for_surface(
        packets,
        "export",
        include_blocked_quality=request.include_blocked_quality
        or request.candidate_quality_status is not None,
    )
    return (
        packets,
        _database_output_path(artifact_type, suffix),
        {
            "candidate_count": len(packets),
            "filter_snapshot": {
                **filters,
                "locale": resolved_locale,
                "evidence_text_mode": "source_and_translation",
            },
        },
    )


def _output_artifact(
    request: OutputRequest,
    artifact_type: str,
    path: Path,
    artifact_base: dict,
) -> dict:
    if request.scan_run_id is not None:
        artifact = repository.add_output_artifact(
            request.scan_run_id,
            artifact_type,
            str(path),
            filter_snapshot=artifact_base.get("filter_snapshot") or {},
        )
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
        "city_id": (
            packet.entity.city_assignment.city_id
            if packet.entity.city_assignment is not None
            else None
        ),
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
