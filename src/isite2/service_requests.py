from __future__ import annotations

import hashlib
import json
import os
import secrets
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator
from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from isite2.localization import scene_label, t

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ServiceRequestType(StrEnum):
    SCAN_ENHANCEMENT = "scan_enhancement"
    FEATURE_REQUEST = "feature_request"
    PPT_REPORT = "ppt_report"


class ServiceRequestStatus(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DELIVERY_PENDING = "delivery_pending"
    COMPLETED = "completed"


class CityScope(StrEnum):
    SINGLE_CITY = "single_city"
    NATIONAL_MAIN_CITIES = "national_main_cities"


class LocationInputMode(StrEnum):
    CATALOG = "catalog"
    CUSTOM = "custom"
    NOT_APPLICABLE = "not_applicable"


class ScanEnhancementRequest(BaseModel):
    request_type: Literal[ServiceRequestType.SCAN_ENHANCEMENT]
    contact_email: EmailStr
    locale: Literal["en", "zh"] = "en"
    client_request_id: UUID
    country: ShortText
    country_input_mode: LocationInputMode = LocationInputMode.CATALOG
    city_scope: CityScope
    city: ShortText | None = None
    city_input_mode: LocationInputMode = LocationInputMode.CATALOG
    scene_types: list[ShortText] = Field(min_length=1, max_length=32)
    target_new_qualified_properties: int = Field(ge=1, le=10_000)

    @model_validator(mode="before")
    @classmethod
    def lift_legacy_scene_type(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        lifted = dict(value)
        if not lifted.get("scene_types") and lifted.get("scene_type"):
            lifted["scene_types"] = [lifted["scene_type"]]
        return lifted

    @model_validator(mode="after")
    def validate_city_scope(self) -> ScanEnhancementRequest:
        self.scene_types = list(dict.fromkeys(self.scene_types))
        if self.country_input_mode == LocationInputMode.NOT_APPLICABLE:
            raise ValueError("country_input_mode cannot be not_applicable")
        if self.city_scope == CityScope.SINGLE_CITY and not self.city:
            raise ValueError("city is required for single_city scope")
        if (
            self.city_scope == CityScope.SINGLE_CITY
            and self.city_input_mode == LocationInputMode.NOT_APPLICABLE
        ):
            raise ValueError("city_input_mode cannot be not_applicable for single_city scope")
        if self.city_scope == CityScope.NATIONAL_MAIN_CITIES:
            self.city = None
            self.city_input_mode = LocationInputMode.NOT_APPLICABLE
        return self


class FeatureRequest(BaseModel):
    request_type: Literal[ServiceRequestType.FEATURE_REQUEST]
    contact_email: EmailStr
    locale: Literal["en", "zh"] = "en"
    client_request_id: UUID
    title: ShortText
    current_workflow: LongText
    requested_flow: LongText
    expected_outcome: LongText


class PptReportRequest(BaseModel):
    request_type: Literal[ServiceRequestType.PPT_REPORT]
    contact_email: EmailStr
    locale: Literal["en", "zh"] = "en"
    client_request_id: UUID
    country: ShortText
    report_locale: Literal["en", "zh"]


ServiceRequestCreate = Annotated[
    ScanEnhancementRequest | FeatureRequest | PptReportRequest,
    Field(discriminator="request_type"),
]


class RequestDecision(BaseModel):
    actor: ShortText = "owner"
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class RequestBlock(BaseModel):
    actor: ShortText = "owner"
    reason: LongText


class ProductUpdateCreate(BaseModel):
    category: Literal["scan", "feature"]
    title_en: ShortText
    title_zh: ShortText
    summary_en: LongText
    summary_zh: LongText
    country: ShortText | None = None
    city: ShortText | None = None
    scene_type: ShortText | None = None
    scene_types: list[ShortText] = Field(default_factory=list, max_length=32)
    actual_new_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def normalize_scene_types(self) -> ProductUpdateCreate:
        self.scene_types = list(dict.fromkeys(self.scene_types))
        if self.scene_type and not self.scene_types:
            self.scene_types = [self.scene_type]
        elif self.scene_type and self.scene_type not in self.scene_types:
            raise ValueError("scene_type must be included in scene_types")
        if not self.scene_type and len(self.scene_types) == 1:
            self.scene_type = self.scene_types[0]
        return self


class CompletionPrepare(BaseModel):
    actor: ShortText = "codex"
    execution_result: dict[str, Any]
    product_update: ProductUpdateCreate | None = None
    attachment_path: str | None = None
    attachment_sha256: str | None = None
    attachment_size: int | None = Field(default=None, ge=0)


class DeliveryResult(BaseModel):
    actor: ShortText = "codex"
    sent: bool
    error: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class OpsBase(DeclarativeBase):
    pass


class ServiceRequestDB(OpsBase):
    __tablename__ = "service_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    submitter_username: Mapped[str] = mapped_column(String(128), nullable=False)
    submitter_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_email_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ui_locale: Mapped[str] = mapped_column(String(8), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_actor: Mapped[str | None] = mapped_column(String(128))
    approval_note: Mapped[str | None] = mapped_column(Text)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (Index("idx_service_requests_status_submitted", "status", "submitted_at"),)


class ServiceRequestEventDB(OpsBase):
    __tablename__ = "service_request_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceRequestDeliveryDB(OpsBase):
    __tablename__ = "service_request_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    template_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    attachment_path: Mapped[str | None] = mapped_column(Text)
    attachment_sha256: Mapped[str | None] = mapped_column(String(64))
    attachment_size: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("request_id", "template_kind", name="uq_request_delivery_template"),
    )


class ProductUpdateDB(OpsBase):
    __tablename__ = "product_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_request_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title_en: Mapped[str] = mapped_column(String(256), nullable=False)
    title_zh: Mapped[str] = mapped_column(String(256), nullable=False)
    summary_en: Mapped[str] = mapped_column(Text, nullable=False)
    summary_zh: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    scene_type: Mapped[str | None] = mapped_column(String(128))
    scene_types: Mapped[list[str] | None] = mapped_column(JSON)
    actual_new_count: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


def default_ops_database_url() -> str:
    return os.getenv("ISITE2_OPS_DATABASE_URL", "sqlite+pysqlite:///outputs/isite2_ops.db")


@lru_cache(maxsize=1)
def get_service_request_repository() -> ServiceRequestRepository:
    return ServiceRequestRepository()


class ServiceRequestRepository:
    def __init__(self, url: str | None = None) -> None:
        resolved = url or default_ops_database_url()
        connect_args = {"check_same_thread": False} if resolved.startswith("sqlite") else {}
        self.engine = create_engine(
            resolved, future=True, pool_pre_ping=True, connect_args=connect_args
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True
        )
        OpsBase.metadata.create_all(self.engine)
        self._ensure_ops_schema()

    def _ensure_ops_schema(self) -> None:
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("product_updates")
        }
        if "scene_types" not in columns:
            with self.engine.begin() as connection:
                connection.execute(text("ALTER TABLE product_updates ADD COLUMN scene_types JSON"))
        with self.engine.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, scene_type FROM product_updates "
                    "WHERE scene_types IS NULL AND scene_type IS NOT NULL"
                )
            ).all()
            for row in rows:
                connection.execute(
                    update(ProductUpdateDB)
                    .where(ProductUpdateDB.id == row.id)
                    .values(scene_types=[row.scene_type])
                )

    def clear(self) -> None:
        with self.session_factory() as session:
            for model in (
                ServiceRequestEventDB,
                ServiceRequestDeliveryDB,
                ProductUpdateDB,
                ServiceRequestDB,
            ):
                session.query(model).delete()
            session.commit()

    def count_recent(self, fingerprint: str, *, hours: int = 24) -> int:
        since = _now() - timedelta(hours=hours)
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count(ServiceRequestDB.id)).where(
                        ServiceRequestDB.submitter_fingerprint == fingerprint,
                        ServiceRequestDB.submitted_at >= since,
                    )
                )
                or 0
            )

    def has_client_request_id(self, client_request_id: UUID | str) -> bool:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(ServiceRequestDB.id).where(
                        ServiceRequestDB.client_request_id == str(client_request_id)
                    )
                )
                is not None
            )

    def create_request(
        self,
        payload: ScanEnhancementRequest | FeatureRequest | PptReportRequest,
        *,
        username: str,
        fingerprint: str,
    ) -> tuple[ServiceRequestDB, bool]:
        request_payload = payload.model_dump(mode="json", exclude={"contact_email"})
        payload_hash = _sha256_text(_canonical_json(request_payload))
        client_request_id = str(payload.client_request_id)
        email = str(payload.contact_email).strip().casefold()
        with self.session_factory() as session:
            existing = session.scalar(
                select(ServiceRequestDB).where(
                    ServiceRequestDB.client_request_id == client_request_id
                )
            )
            if existing is not None:
                if existing.request_payload_hash != payload_hash or existing.contact_email != email:
                    raise ValueError("client_request_id already belongs to a different request")
                return existing, False
            now = _now()
            row = ServiceRequestDB(
                id=str(uuid4()),
                request_code=self._new_request_code(session, now),
                request_type=str(payload.request_type),
                status=ServiceRequestStatus.SUBMITTED,
                submitter_username=username,
                submitter_fingerprint=fingerprint,
                contact_email=email,
                contact_email_hash=_sha256_text(email),
                ui_locale=payload.locale,
                request_payload=request_payload,
                request_payload_hash=payload_hash,
                client_request_id=client_request_id,
                submitted_at=now,
                updated_at=now,
            )
            session.add(row)
            self._add_event(session, row.id, "submitted", username, {})
            session.commit()
            session.refresh(row)
            return row, True

    def list_requests(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 200,
        include_email: bool = False,
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            statement = select(ServiceRequestDB)
            if statuses:
                statement = statement.where(ServiceRequestDB.status.in_(statuses))
            rows = session.scalars(
                statement.order_by(ServiceRequestDB.submitted_at.asc()).limit(limit)
            ).all()
            return [self._serialize_request(row, include_email=include_email) for row in rows]

    def get_request(
        self, request_code: str, *, include_email: bool = False
    ) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = self._request_by_code(session, request_code)
            return self._serialize_request(row, include_email=include_email) if row else None

    def transition(
        self,
        request_code: str,
        target: ServiceRequestStatus,
        *,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        allowed = {
            ServiceRequestStatus.SUBMITTED: {
                ServiceRequestStatus.APPROVED,
                ServiceRequestStatus.REJECTED,
            },
            ServiceRequestStatus.APPROVED: {
                ServiceRequestStatus.IN_PROGRESS,
                ServiceRequestStatus.REJECTED,
            },
            ServiceRequestStatus.IN_PROGRESS: {
                ServiceRequestStatus.BLOCKED,
                ServiceRequestStatus.DELIVERY_PENDING,
            },
            ServiceRequestStatus.BLOCKED: {
                ServiceRequestStatus.IN_PROGRESS,
                ServiceRequestStatus.REJECTED,
            },
            ServiceRequestStatus.DELIVERY_PENDING: {ServiceRequestStatus.COMPLETED},
        }
        with self.session_factory() as session:
            row = self._require_request(session, request_code)
            current = ServiceRequestStatus(row.status)
            if target not in allowed.get(current, set()):
                raise ValueError(f"invalid transition: {current} -> {target}")
            now = _now()
            row.status = target
            row.updated_at = now
            if target == ServiceRequestStatus.APPROVED:
                row.approved_at = now
                row.approval_actor = actor
                row.approval_note = note
            elif target == ServiceRequestStatus.IN_PROGRESS and row.started_at is None:
                row.started_at = now
            elif target == ServiceRequestStatus.REJECTED:
                row.approval_actor = actor
                row.approval_note = note
            elif target == ServiceRequestStatus.COMPLETED:
                row.completed_at = now
            self._add_event(session, row.id, str(target), actor, {"note": note} if note else {})
            session.commit()
            return self._serialize_request(row, include_email=False)

    def prepare_completion(self, request_code: str, payload: CompletionPrepare) -> dict[str, Any]:
        with self.session_factory() as session:
            row = self._require_request(session, request_code)
            if row.status not in {
                ServiceRequestStatus.IN_PROGRESS,
                ServiceRequestStatus.DELIVERY_PENDING,
            }:
                raise ValueError("request must be in_progress or delivery_pending")
            self._validate_completion(row, payload)
            now = _now()
            row.execution_result = payload.execution_result
            row.status = ServiceRequestStatus.DELIVERY_PENDING
            row.updated_at = now
            if payload.product_update is not None:
                existing_update = session.scalar(
                    select(ProductUpdateDB).where(ProductUpdateDB.source_request_id == row.id)
                )
                if existing_update is None:
                    update = payload.product_update
                    session.add(
                        ProductUpdateDB(
                            id=str(uuid4()),
                            source_request_id=row.id,
                            category=update.category,
                            title_en=update.title_en,
                            title_zh=update.title_zh,
                            summary_en=update.summary_en,
                            summary_zh=update.summary_zh,
                            country=update.country,
                            city=update.city,
                            scene_type=update.scene_type,
                            scene_types=update.scene_types or None,
                            actual_new_count=update.actual_new_count,
                            published_at=now,
                        )
                    )
            delivery = session.scalar(
                select(ServiceRequestDeliveryDB).where(
                    ServiceRequestDeliveryDB.request_id == row.id,
                    ServiceRequestDeliveryDB.template_kind == "completion",
                )
            )
            if delivery is None:
                delivery = ServiceRequestDeliveryDB(
                    id=str(uuid4()),
                    request_id=row.id,
                    template_kind="completion",
                    recipient_email=row.contact_email,
                    attachment_path=payload.attachment_path,
                    attachment_sha256=payload.attachment_sha256,
                    attachment_size=payload.attachment_size,
                    attempt_count=0,
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                session.add(delivery)
            self._add_event(session, row.id, "delivery_pending", payload.actor, {})
            session.commit()
            return self._serialize_request(row, include_email=True)

    def delivery_payload(self, request_code: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = self._require_request(session, request_code)
            delivery = session.scalar(
                select(ServiceRequestDeliveryDB).where(
                    ServiceRequestDeliveryDB.request_id == row.id,
                    ServiceRequestDeliveryDB.template_kind == "completion",
                )
            )
            if delivery is None:
                raise ValueError("completion delivery is not prepared")
            return {
                "request": self._serialize_request(row, include_email=True),
                "delivery": self._serialize_delivery(delivery),
            }

    def record_delivery(self, request_code: str, result: DeliveryResult) -> dict[str, Any]:
        with self.session_factory() as session:
            row = self._require_request(session, request_code)
            delivery = session.scalar(
                select(ServiceRequestDeliveryDB).where(
                    ServiceRequestDeliveryDB.request_id == row.id,
                    ServiceRequestDeliveryDB.template_kind == "completion",
                )
            )
            if delivery is None:
                raise ValueError("completion delivery is not prepared")
            if delivery.status == "sent":
                return self._serialize_request(row, include_email=False)
            now = _now()
            delivery.attempt_count += 1
            delivery.updated_at = now
            if result.sent:
                delivery.status = "sent"
                delivery.sent_at = now
                delivery.last_error = None
                row.status = ServiceRequestStatus.COMPLETED
                row.completed_at = now
                row.updated_at = now
                self._add_event(session, row.id, "completed", result.actor, {})
            else:
                delivery.status = "failed"
                delivery.last_error = result.error or "SMTP delivery failed"
                row.status = ServiceRequestStatus.DELIVERY_PENDING
                row.updated_at = now
                self._add_event(
                    session, row.id, "delivery_failed", result.actor, {"error": delivery.last_error}
                )
            session.commit()
            return self._serialize_request(row, include_email=False)

    def list_updates(self, locale: str, *, limit: int = 30) -> list[dict[str, Any]]:
        resolved = "zh" if locale == "zh" else "en"
        with self.session_factory() as session:
            rows = session.scalars(
                select(ProductUpdateDB)
                .order_by(ProductUpdateDB.published_at.desc())
                .limit(max(1, min(limit, 50)))
            ).all()
            result = []
            for row in rows:
                scene_types = list(row.scene_types or [])
                if not scene_types and row.scene_type:
                    scene_types = [row.scene_type]
                result.append(
                    {
                    "id": row.id,
                    "category": row.category,
                    "title": row.title_zh if resolved == "zh" else row.title_en,
                    "summary": row.summary_zh if resolved == "zh" else row.summary_en,
                    "country": row.country,
                    "city": row.city,
                    "scene_type": row.scene_type,
                    "scene_label": scene_label(row.scene_type, resolved)
                    if row.scene_type
                    else None,
                    "scene_types": scene_types,
                    "scene_labels": [scene_label(item, resolved) for item in scene_types],
                    "actual_new_count": row.actual_new_count,
                    "published_at": row.published_at.isoformat(),
                    }
                )
            return result

    def _validate_completion(self, row: ServiceRequestDB, payload: CompletionPrepare) -> None:
        result = payload.execution_result
        if row.request_type in {
            ServiceRequestType.SCAN_ENHANCEMENT,
            ServiceRequestType.FEATURE_REQUEST,
        }:
            if result.get("release_verified") is not True:
                raise ValueError("release_verified=true is required before publication")
            if payload.product_update is None:
                raise ValueError("scan and feature completions require a product update")
            expected = (
                "scan" if row.request_type == ServiceRequestType.SCAN_ENHANCEMENT else "feature"
            )
            if payload.product_update.category != expected:
                raise ValueError(f"product update category must be {expected}")
        if row.request_type == ServiceRequestType.SCAN_ENHANCEMENT:
            self._validate_scan_completion(row, payload)
        if row.request_type == ServiceRequestType.PPT_REPORT:
            if result.get("report_qa_passed") is not True:
                raise ValueError("report_qa_passed=true is required")
            if payload.product_update is not None:
                raise ValueError("PPT completion must not create a product update")
            if not payload.attachment_path or not payload.attachment_sha256:
                raise ValueError("audited PPT attachment metadata is required")
            if payload.attachment_size is None or payload.attachment_size > 20 * 1024 * 1024:
                raise ValueError("PPT attachment exceeds the 20 MB limit")

    @staticmethod
    def _validate_scan_completion(row: ServiceRequestDB, payload: CompletionPrepare) -> None:
        request_payload = row.request_payload or {}
        requested_scenes = list(request_payload.get("scene_types") or [])
        if not requested_scenes and request_payload.get("scene_type"):
            requested_scenes = [request_payload["scene_type"]]
        update = payload.product_update
        if update is None:
            return
        if set(update.scene_types) != set(requested_scenes):
            raise ValueError("product update scene_types must match the request")
        target = request_payload.get("target_new_qualified_properties")
        result = payload.execution_result
        if result.get("target_new_qualified_properties") != target:
            raise ValueError("completion target must match the request")
        actual = result.get("actual_new_qualified_properties")
        shortfall = result.get("shortfall")
        if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0:
            raise ValueError("actual_new_qualified_properties must be a non-negative integer")
        expected_shortfall = max(int(target) - actual, 0)
        if shortfall != expected_shortfall:
            raise ValueError("completion shortfall is inconsistent with target and actual")
        if update.actual_new_count != actual:
            raise ValueError("product update actual_new_count must match completion actual")
        if len(requested_scenes) <= 1:
            return
        scene_results = result.get("scene_results")
        if not isinstance(scene_results, list) or not scene_results:
            raise ValueError("multi-scene completion requires scene_results")
        seen: set[str] = set()
        distributed_actual = 0
        for item in scene_results:
            if not isinstance(item, dict):
                raise ValueError("scene_results entries must be objects")
            scene_type = item.get("scene_type")
            scene_actual = item.get("actual_new_qualified_properties")
            if scene_type not in requested_scenes or scene_type in seen:
                raise ValueError("scene_results must contain unique requested scene types")
            if (
                not isinstance(scene_actual, int)
                or isinstance(scene_actual, bool)
                or scene_actual < 0
            ):
                raise ValueError("scene result actual must be a non-negative integer")
            seen.add(scene_type)
            distributed_actual += scene_actual
        if seen != set(requested_scenes):
            raise ValueError("scene_results must cover every requested scene type")
        if distributed_actual != actual:
            raise ValueError("scene_results actual total must match completion actual")

    @staticmethod
    def _new_request_code(session: Session, now: datetime) -> str:
        for _ in range(20):
            code = f"SR-{now.strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"
            if (
                session.scalar(
                    select(ServiceRequestDB.id).where(ServiceRequestDB.request_code == code)
                )
                is None
            ):
                return code
        raise RuntimeError("unable to allocate request code")

    @staticmethod
    def _request_by_code(session: Session, request_code: str) -> ServiceRequestDB | None:
        return session.scalar(
            select(ServiceRequestDB).where(ServiceRequestDB.request_code == request_code.strip())
        )

    def _require_request(self, session: Session, request_code: str) -> ServiceRequestDB:
        row = self._request_by_code(session, request_code)
        if row is None:
            raise LookupError("service request not found")
        return row

    @staticmethod
    def _add_event(
        session: Session,
        request_id: str,
        event_type: str,
        actor: str,
        metadata: dict[str, Any],
    ) -> None:
        session.add(
            ServiceRequestEventDB(
                id=str(uuid4()),
                request_id=request_id,
                event_type=event_type,
                actor=actor,
                event_metadata=metadata,
                created_at=_now(),
            )
        )

    @staticmethod
    def _serialize_request(row: ServiceRequestDB, *, include_email: bool) -> dict[str, Any]:
        return {
            "request_code": row.request_code,
            "request_type": row.request_type,
            "status": row.status,
            "submitter_username": row.submitter_username,
            "contact_email": row.contact_email if include_email else mask_email(row.contact_email),
            "locale": row.ui_locale,
            "request_payload": row.request_payload,
            "submitted_at": row.submitted_at.isoformat(),
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "approval_actor": row.approval_actor,
            "approval_note": row.approval_note,
            "execution_result": row.execution_result,
        }

    @staticmethod
    def _serialize_delivery(row: ServiceRequestDeliveryDB) -> dict[str, Any]:
        return {
            "status": row.status,
            "attempt_count": row.attempt_count,
            "attachment_path": row.attachment_path,
            "attachment_sha256": row.attachment_sha256,
            "attachment_size": row.attachment_size,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        }


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str

    @classmethod
    def from_environment(cls) -> SmtpSettings:
        host = os.getenv("ISITE2_SMTP_HOST", "smtp.qq.com")
        port = int(os.getenv("ISITE2_SMTP_PORT", "465"))
        username = os.getenv("ISITE2_SMTP_USERNAME", "").strip()
        password = os.getenv("ISITE2_SMTP_PASSWORD", "")
        sender = os.getenv("ISITE2_SMTP_SENDER", username).strip()
        if not username or not password or not sender:
            raise RuntimeError("SMTP credentials are not configured")
        return cls(host=host, port=port, username=username, password=password, sender=sender)


class CompletionMailer:
    def __init__(self, settings: SmtpSettings, smtp_factory: Any = smtplib.SMTP_SSL) -> None:
        self.settings = settings
        self.smtp_factory = smtp_factory

    def send(self, payload: dict[str, Any]) -> None:
        request = payload["request"]
        delivery = payload["delivery"]
        locale = request.get("locale") or "en"
        message = EmailMessage()
        message["Subject"] = t(
            "email.request_completed_subject",
            locale,
            default="iSite2 request {request_code} completed",
            request_code=request["request_code"],
        )
        message["From"] = self.settings.sender
        message["To"] = request["contact_email"]
        message.set_content(
            t(
                "email.request_completed_body",
                locale,
                default=(
                    "Your iSite2 request {request_code} has been completed.\n\n"
                    "Completion summary:\n{summary}\n"
                ),
                request_code=request["request_code"],
                summary=_canonical_json(request.get("execution_result") or {}),
            )
        )
        attachment_path = delivery.get("attachment_path")
        if attachment_path:
            path = Path(attachment_path)
            data = path.read_bytes()
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != delivery.get("attachment_sha256"):
                raise ValueError("PPT attachment checksum changed after QA")
            if len(data) > 20 * 1024 * 1024:
                raise ValueError("PPT attachment exceeds the 20 MB limit")
            message.add_attachment(
                data,
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.presentationml.presentation",
                filename=path.name,
            )
        with self.smtp_factory(self.settings.host, self.settings.port, timeout=30) as smtp:
            smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(message)


def load_secret_environment(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
