from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from isite2.service_requests import (  # noqa: E402
    CompletionMailer,
    SmtpSettings,
    load_secret_environment,
)

DEFAULT_SECRET_FILE = ROOT / ".secrets" / "service_requests.env"


def main() -> int:
    parser = argparse.ArgumentParser(description="Review and deliver iSite2 service requests.")
    parser.add_argument(
        "--api-base", default=os.getenv("ISITE2_REQUEST_API_BASE", "https://isite.cloud")
    )
    parser.add_argument("--secret-file", type=Path, default=DEFAULT_SECRET_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    digest = subparsers.add_parser("digest")
    digest.add_argument(
        "--status",
        default="submitted,approved,blocked,delivery_pending",
        help="Comma-separated statuses.",
    )

    for command in ("approve", "reject", "start"):
        child = subparsers.add_parser(command)
        child.add_argument("request_code")
        child.add_argument("--actor", default="owner" if command != "start" else "codex")
        child.add_argument("--note", default="")

    blocked = subparsers.add_parser("block")
    blocked.add_argument("request_code")
    blocked.add_argument("--actor", default="codex")
    blocked.add_argument("--reason", required=True)

    complete = subparsers.add_parser("complete")
    complete.add_argument("request_code")
    complete.add_argument("--actor", default="codex")
    complete.add_argument("--result-json", type=Path, required=True)
    complete.add_argument("--update-json", type=Path)
    complete.add_argument("--attachment", type=Path)

    retry = subparsers.add_parser("retry-delivery")
    retry.add_argument("request_code")
    retry.add_argument("--actor", default="codex")

    args = parser.parse_args()
    load_secret_environment(args.secret_file)
    token = os.getenv("ISITE2_REQUEST_ADMIN_TOKEN", "").strip()
    if not token:
        raise SystemExit("ISITE2_REQUEST_ADMIN_TOKEN is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    api_base = args.api_base.rstrip("/")

    with httpx.Client(base_url=api_base, headers=headers, timeout=60.0) as client:
        if args.command == "digest":
            payload = _request(
                client, "GET", "/admin/service-requests", params={"status": args.status}
            )
            print(json.dumps(_digest_payload(payload), ensure_ascii=False, indent=2))
            return 0
        if args.command in {"approve", "reject", "start"}:
            payload = _request(
                client,
                "POST",
                f"/admin/service-requests/{args.request_code}/{args.command}",
                json={"actor": args.actor, "note": args.note},
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "block":
            payload = _request(
                client,
                "POST",
                f"/admin/service-requests/{args.request_code}/block",
                json={"actor": args.actor, "reason": args.reason},
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "complete":
            completion = {
                "actor": args.actor,
                "execution_result": _read_json_object(args.result_json),
                "product_update": _read_json_object(args.update_json) if args.update_json else None,
            }
            if args.attachment:
                attachment = args.attachment.resolve()
                data = attachment.read_bytes()
                completion.update(
                    {
                        "attachment_path": str(attachment),
                        "attachment_sha256": hashlib.sha256(data).hexdigest(),
                        "attachment_size": len(data),
                    }
                )
            _request(
                client,
                "POST",
                f"/admin/service-requests/{args.request_code}/prepare-completion",
                json=completion,
            )
            _deliver(client, args.request_code, args.actor)
            return 0
        if args.command == "retry-delivery":
            _deliver(client, args.request_code, args.actor)
            return 0
    return 0


def _deliver(client: httpx.Client, request_code: str, actor: str) -> None:
    payload = _request(
        client,
        "GET",
        f"/admin/service-requests/{request_code}/delivery",
    )
    if payload["delivery"]["status"] == "sent":
        print(json.dumps({"request_code": request_code, "status": "already_sent"}))
        return
    try:
        CompletionMailer(SmtpSettings.from_environment()).send(payload)
    except Exception as exc:
        _request(
            client,
            "POST",
            f"/admin/service-requests/{request_code}/delivery-result",
            json={"actor": actor, "sent": False, "error": _safe_error(exc)},
        )
        raise
    completed = _request(
        client,
        "POST",
        f"/admin/service-requests/{request_code}/delivery-result",
        json={"actor": actor, "sent": True, "error": ""},
    )
    print(json.dumps(completed, ensure_ascii=False, indent=2))


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    if response.is_error:
        detail = response.text[:1000]
        raise RuntimeError(f"admin API {method} {path} failed ({response.status_code}): {detail}")
    return response.json()


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("requests") or []
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        request_type = str(row.get("request_type") or "unknown")
        request_payload = row.get("request_payload") or {}
        summary = {
            "request_code": row.get("request_code"),
            "status": row.get("status"),
            "submitted_at": row.get("submitted_at"),
            "contact_email": row.get("contact_email"),
            "request": request_payload,
        }
        if request_type == "scan_enhancement":
            summary["scan_scope"] = _scan_scope_summary(request_payload)
        groups.setdefault(request_type, []).append(summary)
    return {"count": len(rows), "groups": groups}


def _scan_scope_summary(request_payload: dict[str, Any]) -> dict[str, Any]:
    scene_types = list(dict.fromkeys(request_payload.get("scene_types") or []))
    if not scene_types and request_payload.get("scene_type"):
        scene_types = [request_payload["scene_type"]]
    country_mode = request_payload.get("country_input_mode") or "catalog"
    city_mode = request_payload.get("city_input_mode") or (
        "not_applicable"
        if request_payload.get("city_scope") == "national_main_cities"
        else "catalog"
    )
    return {
        "country": request_payload.get("country"),
        "country_input_mode": country_mode,
        "city_scope": request_payload.get("city_scope"),
        "city": request_payload.get("city"),
        "city_input_mode": city_mode,
        "scene_types": scene_types,
        "combined_target_new_qualified_properties": request_payload.get(
            "target_new_qualified_properties"
        ),
        "requires_location_verification": "custom" in {country_mode, city_mode},
    }


def _safe_error(exc: Exception) -> str:
    value = f"{type(exc).__name__}: {exc}"
    return value[:1000]


if __name__ == "__main__":
    raise SystemExit(main())
