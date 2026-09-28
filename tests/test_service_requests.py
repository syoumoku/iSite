from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from scripts.publish_public_snapshot import DISPLAY_TABLES, PUBLIC_EMPTY_TABLES
from scripts.service_request_admin import _digest_payload
from sqlalchemy import create_engine, inspect, text

import isite2.api.main as api_main
from isite2.api.main import app
from isite2.service_requests import (
    CompletionMailer,
    CompletionPrepare,
    ProductUpdateCreate,
    ScanEnhancementRequest,
    ServiceRequestRepository,
    ServiceRequestStatus,
    SmtpSettings,
    get_service_request_repository,
)


def _scan_payload(client_request_id=None) -> ScanEnhancementRequest:
    return ScanEnhancementRequest(
        request_type="scan_enhancement",
        contact_email="user@example.com",
        locale="zh",
        client_request_id=client_request_id or uuid4(),
        country="Egypt",
        country_input_mode="catalog",
        city_scope="single_city",
        city="Cairo",
        city_input_mode="catalog",
        scene_types=["stadium"],
        target_new_qualified_properties=10,
    )


def test_scan_request_contract_and_idempotent_storage(tmp_path: Path) -> None:
    repository = ServiceRequestRepository(f"sqlite+pysqlite:///{tmp_path / 'ops.db'}")
    payload = _scan_payload()

    row, created = repository.create_request(payload, username="visitor", fingerprint="fp")
    repeated, repeated_created = repository.create_request(
        payload, username="visitor", fingerprint="fp"
    )

    assert created is True
    assert repeated_created is False
    assert repeated.id == row.id
    assert row.request_code.startswith("SR-")
    assert repository.list_requests()[0]["contact_email"] == "u***@example.com"
    assert repository.get_request(row.request_code, include_email=True)["contact_email"] == (
        "user@example.com"
    )
    with pytest.raises(ValidationError, match="city is required"):
        ScanEnhancementRequest(
            **{
                **payload.model_dump(),
                "client_request_id": uuid4(),
                "city_scope": "single_city",
                "city": None,
            }
        )

    legacy = ScanEnhancementRequest.model_validate(
        {
            **payload.model_dump(mode="json", exclude={"scene_types"}),
            "client_request_id": str(uuid4()),
            "scene_type": "stadium",
        }
    )
    assert legacy.scene_types == ["stadium"]


def test_scan_request_normalizes_multi_scene_and_custom_location() -> None:
    payload = ScanEnhancementRequest(
        request_type="scan_enhancement",
        contact_email="user@example.com",
        locale="en",
        client_request_id=uuid4(),
        country="Atlantis",
        country_input_mode="custom",
        city_scope="single_city",
        city="Poseidon",
        city_input_mode="custom",
        scene_types=["stadium", "airport_terminal", "stadium"],
        target_new_qualified_properties=20,
    )
    assert payload.scene_types == ["stadium", "airport_terminal"]
    assert payload.country_input_mode == "custom"
    assert payload.city_input_mode == "custom"

    national = ScanEnhancementRequest(
        **{
            **payload.model_dump(),
            "client_request_id": uuid4(),
            "city_scope": "national_main_cities",
        }
    )
    assert national.city is None
    assert national.city_input_mode == "not_applicable"

    with pytest.raises(ValidationError, match="country_input_mode"):
        ScanEnhancementRequest(
            **{
                **payload.model_dump(),
                "client_request_id": uuid4(),
                "country_input_mode": "not_applicable",
            }
        )


def test_digest_flags_custom_location_and_combined_multi_scene_target() -> None:
    digest = _digest_payload(
        {
            "requests": [
                {
                    "request_code": "SR-20260825-ABCD",
                    "status": "submitted",
                    "submitted_at": "2026-08-25T01:00:00Z",
                    "contact_email": "u***@example.com",
                    "request_type": "scan_enhancement",
                    "request_payload": {
                        "country": "Atlantis",
                        "country_input_mode": "custom",
                        "city_scope": "single_city",
                        "city": "Poseidon",
                        "city_input_mode": "custom",
                        "scene_types": ["stadium", "airport_terminal"],
                        "target_new_qualified_properties": 20,
                    },
                }
            ]
        }
    )

    scope = digest["groups"]["scan_enhancement"][0]["scan_scope"]
    assert scope["scene_types"] == ["stadium", "airport_terminal"]
    assert scope["combined_target_new_qualified_properties"] == 20
    assert scope["requires_location_verification"] is True


def test_completion_requires_verified_release_and_is_idempotent(tmp_path: Path) -> None:
    repository = ServiceRequestRepository(f"sqlite+pysqlite:///{tmp_path / 'ops.db'}")
    row, _ = repository.create_request(_scan_payload(), username="visitor", fingerprint="fp")
    repository.transition(row.request_code, ServiceRequestStatus.APPROVED, actor="owner")
    repository.transition(row.request_code, ServiceRequestStatus.IN_PROGRESS, actor="codex")
    update = ProductUpdateCreate(
        category="scan",
        title_en="Egypt scan expanded",
        title_zh="埃及扫网扩充",
        summary_en="Added 8 qualified opportunities in Egypt / Cairo / Stadium; target 10.",
        summary_zh="在埃及 / 开罗 / 体育场新增 8 个合格机会点；目标为 10。",
        country="Egypt",
        city="Cairo",
        scene_types=["stadium"],
        actual_new_count=8,
    )
    with pytest.raises(ValueError, match="release_verified"):
        repository.prepare_completion(
            row.request_code,
            CompletionPrepare(execution_result={"release_verified": False}, product_update=update),
        )

    repository.prepare_completion(
        row.request_code,
        CompletionPrepare(
            execution_result={
                "release_verified": True,
                "target_new_qualified_properties": 10,
                "actual_new_qualified_properties": 8,
                "shortfall": 2,
            },
            product_update=update,
        ),
    )
    assert repository.list_updates("zh")[0]["actual_new_count"] == 8
    assert repository.list_updates("zh")[0]["scene_types"] == ["stadium"]
    completed = repository.record_delivery(
        row.request_code,
        api_main.DeliveryResult(actor="codex", sent=True),
    )
    repeated = repository.record_delivery(
        row.request_code,
        api_main.DeliveryResult(actor="codex", sent=True),
    )
    assert completed["status"] == "completed"
    assert repeated["status"] == "completed"


def test_multi_scene_completion_requires_consistent_distribution(tmp_path: Path) -> None:
    repository = ServiceRequestRepository(f"sqlite+pysqlite:///{tmp_path / 'ops.db'}")
    request = ScanEnhancementRequest(
        request_type="scan_enhancement",
        contact_email="user@example.com",
        locale="en",
        client_request_id=uuid4(),
        country="Egypt",
        city_scope="national_main_cities",
        scene_types=["airport_terminal", "stadium"],
        target_new_qualified_properties=20,
    )
    row, _ = repository.create_request(request, username="visitor", fingerprint="fp")
    repository.transition(row.request_code, ServiceRequestStatus.APPROVED, actor="owner")
    repository.transition(row.request_code, ServiceRequestStatus.IN_PROGRESS, actor="codex")
    update = ProductUpdateCreate(
        category="scan",
        title_en="Egypt scan expanded",
        title_zh="埃及扫网扩充",
        summary_en="Added 17 qualified opportunities.",
        summary_zh="新增 17 个合格机会点。",
        country="Egypt",
        scene_types=["airport_terminal", "stadium"],
        actual_new_count=17,
    )
    completion = CompletionPrepare(
        execution_result={
            "release_verified": True,
            "target_new_qualified_properties": 20,
            "actual_new_qualified_properties": 17,
            "shortfall": 3,
            "scene_results": [
                {"scene_type": "airport_terminal", "actual_new_qualified_properties": 7},
                {"scene_type": "stadium", "actual_new_qualified_properties": 10},
            ],
        },
        product_update=update,
    )
    repository.prepare_completion(row.request_code, completion)
    listed = repository.list_updates("en")[0]
    assert listed["scene_types"] == ["airport_terminal", "stadium"]
    assert listed["scene_labels"] == ["Airport", "Stadium"]

    broken = completion.model_copy(deep=True)
    broken.execution_result["scene_results"][1]["actual_new_qualified_properties"] = 9
    with pytest.raises(ValueError, match="actual total"):
        repository._validate_scan_completion(row, broken)


def test_ops_schema_migration_backfills_legacy_scene_type(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE product_updates ("
                "id VARCHAR(36) PRIMARY KEY, source_request_id VARCHAR(36) UNIQUE NOT NULL, "
                "category VARCHAR(16) NOT NULL, title_en VARCHAR(256) NOT NULL, "
                "title_zh VARCHAR(256) NOT NULL, summary_en TEXT NOT NULL, "
                "summary_zh TEXT NOT NULL, "
                "country VARCHAR(128), city VARCHAR(128), scene_type VARCHAR(128), "
                "actual_new_count INTEGER, published_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO product_updates VALUES ("
                "'u1','r1','scan','Expanded','扩充','Summary','摘要','Egypt','Cairo',"
                "'stadium',8,'2026-08-25T00:00:00+00:00')"
            )
        )
    repository = ServiceRequestRepository(url)
    assert "scene_types" in {
        column["name"] for column in inspect(repository.engine).get_columns("product_updates")
    }
    assert repository.list_updates("en")[0]["scene_types"] == ["stadium"]


class _FakeSmtp:
    messages = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, username: str, password: str) -> None:
        assert username == "sender@example.com"
        assert password == "secret"

    def send_message(self, message) -> None:
        self.messages.append(message)


def test_completion_mailer_attaches_only_checksum_verified_ppt(tmp_path: Path) -> None:
    attachment = tmp_path / "report.pptx"
    attachment.write_bytes(b"ppt-data")
    digest = hashlib.sha256(b"ppt-data").hexdigest()
    payload = {
        "request": {
            "request_code": "SR-20260825-ABCD",
            "contact_email": "user@example.com",
            "locale": "en",
            "execution_result": {"report_qa_passed": True},
        },
        "delivery": {
            "attachment_path": str(attachment),
            "attachment_sha256": digest,
        },
    }
    _FakeSmtp.messages.clear()
    mailer = CompletionMailer(
        SmtpSettings(
            host="smtp.qq.com",
            port=465,
            username="sender@example.com",
            password="secret",
            sender="sender@example.com",
        ),
        smtp_factory=_FakeSmtp,
    )

    mailer.send(payload)

    assert len(_FakeSmtp.messages) == 1
    assert _FakeSmtp.messages[0].get_filename() is None
    assert any(part.get_filename() == "report.pptx" for part in _FakeSmtp.messages[0].walk())
    payload["delivery"]["attachment_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        mailer.send(payload)


def test_public_api_requires_login_and_never_lists_email(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ISITE2_APP_MODE", "public_view")
    monkeypatch.setenv("ISITE2_APP_AUTH_ENABLED", "1")
    monkeypatch.setenv("ISITE2_APP_AUTH_USER", "visitor")
    monkeypatch.setenv("ISITE2_APP_AUTH_PASSWORD", "visitor123456")
    monkeypatch.setenv("ISITE2_APP_AUTH_SECRET", "request-test-secret")
    monkeypatch.setenv("ISITE2_REQUEST_ADMIN_TOKEN", "admin-test-token")
    monkeypatch.setenv("ISITE2_OPS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'ops.db'}")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    get_service_request_repository.cache_clear()
    client = TestClient(app)
    payload = _scan_payload().model_dump(mode="json")

    assert client.post("/service-requests", json=payload).status_code == 401
    login = client.post("/auth/login", json={"username": "visitor", "password": "visitor123456"})
    assert login.status_code == 200
    invalid_scene = client.post(
        "/service-requests",
        json={
            **payload,
            "client_request_id": str(uuid4()),
            "scene_types": ["airport_terminal", "unknown_scene"],
        },
        headers={"Origin": "http://testserver"},
    )
    assert invalid_scene.status_code == 422
    assert "unknown scene_types" in invalid_scene.text
    created = client.post(
        "/service-requests",
        json=payload,
        headers={"Origin": "http://testserver"},
    )
    assert created.status_code == 201
    assert "contact_email" not in created.text
    assert (
        client.post(
            "/service-requests",
            json=payload,
            headers={"Origin": "http://testserver"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/service-requests",
            json={**payload, "client_request_id": str(uuid4())},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )

    feature = client.post(
        "/service-requests",
        json={
            "request_type": "feature_request",
            "contact_email": "feature@example.com",
            "locale": "en",
            "client_request_id": str(uuid4()),
            "title": "City comparison",
            "current_workflow": "Review cities one at a time.",
            "requested_flow": "Select two cities and compare their evidence.",
            "expected_outcome": "A consistent side-by-side comparison.",
        },
        headers={"Origin": "http://testserver"},
    )
    ppt = client.post(
        "/service-requests",
        json={
            "request_type": "ppt_report",
            "contact_email": "ppt@example.com",
            "locale": "zh",
            "client_request_id": str(uuid4()),
            "country": "Egypt",
            "report_locale": "zh",
        },
        headers={"Origin": "http://testserver"},
    )
    assert feature.status_code == 201
    assert ppt.status_code == 201

    assert client.get("/admin/service-requests").status_code == 403
    admin = client.get(
        "/admin/service-requests",
        headers={"Authorization": "Bearer admin-test-token"},
    )
    assert admin.status_code == 200
    assert admin.json()["count"] == 3
    assert {row["contact_email"] for row in admin.json()["requests"]} == {
        "u***@example.com",
        "f***@example.com",
        "p***@example.com",
    }
    assert client.get("/updates", params={"locale": "zh"}).json() == {
        "locale": "zh",
        "count": 0,
        "updates": [],
    }
    get_service_request_repository().engine.dispose()
    get_service_request_repository.cache_clear()


def test_operational_tables_are_not_part_of_public_snapshot() -> None:
    public_tables = set(DISPLAY_TABLES) | set(PUBLIC_EMPTY_TABLES)
    assert {
        "service_requests",
        "service_request_events",
        "service_request_deliveries",
        "product_updates",
    }.isdisjoint(public_tables)
