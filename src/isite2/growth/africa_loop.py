from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isite2.growth.derived_refresh_trigger import refresh_derived_after_scan
from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.output.excel import write_excel_skeleton
from isite2.repositories import get_default_repository
from isite2.repositories.interfaces import ScanRunRepository
from isite2.rules.config_loader import load_source_registry

AFRICAN_COUNTRIES = [
    "Algeria",
    "Angola",
    "Benin",
    "Botswana",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cameroon",
    "Central African Republic",
    "Chad",
    "Comoros",
    "Congo",
    "Cote d'Ivoire",
    "Democratic Republic of the Congo",
    "Djibouti",
    "Egypt",
    "Equatorial Guinea",
    "Eritrea",
    "Eswatini",
    "Ethiopia",
    "Gabon",
    "Gambia",
    "Ghana",
    "Guinea",
    "Guinea-Bissau",
    "Kenya",
    "Lesotho",
    "Liberia",
    "Libya",
    "Madagascar",
    "Malawi",
    "Mali",
    "Mauritania",
    "Mauritius",
    "Morocco",
    "Mozambique",
    "Namibia",
    "Niger",
    "Nigeria",
    "Rwanda",
    "Sao Tome and Principe",
    "Senegal",
    "Seychelles",
    "Sierra Leone",
    "Somalia",
    "South Africa",
    "South Sudan",
    "Sudan",
    "Tanzania",
    "Togo",
    "Tunisia",
    "Uganda",
    "Zambia",
    "Zimbabwe",
]

DEFAULT_OUTPUT_DIR = Path("outputs") / "africa_scan_loop"
DEFAULT_STATE_PATH = DEFAULT_OUTPUT_DIR / "state.json"


@dataclass(frozen=True)
class AfricaLoopRound:
    round_number: int
    country: str
    run_id: str
    candidate_count: int
    review_count: int
    storage_mode: str | None
    report_path: Path
    summary_path: Path
    excel_path: Path
    improvements: list[str]
    ui_paths: dict[str, str]


def run_africa_scan_round(
    repository: ScanRunRepository | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> AfricaLoopRound:
    """Run one evidence-backed Africa scan round and persist all artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    registry = load_source_registry()
    registry_countries = registry_backed_african_countries(registry)
    if not registry_countries:
        raise RuntimeError("No Africa countries with source_registry candidates are available.")

    state = _load_state(state_path)
    country = _country_for_round(state, registry_countries)
    round_number = int(state.get("round_number", 0)) + 1
    repository = repository or get_default_repository()

    result = run_scan_pipeline(
        {
            "level": "country",
            "regions": ["Africa"],
            "countries": [country],
            "full_scan": True,
            "output_formats": ["excel", "geojson"],
            "custom_filters": {
                "loop_name": "africa_high_value_buildings",
                "registry_backed_only": True,
            },
        },
        repository,
    )
    derived_refresh = refresh_derived_after_scan(repository, result.scan_run.run_id)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = _slug(country)
    excel_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.xlsx"
    summary_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.json"
    report_path = output_dir / f"round_{round_number:04d}_{slug}_{result.scan_run.run_id}.md"

    write_excel_skeleton(excel_path, packets=result.packets)
    repository.add_output_artifact(result.scan_run.run_id, "excel", str(excel_path))

    improvements = _improvement_points(
        registry_countries=registry_countries,
        packets=result.packets,
    )
    summary = _round_summary(
        round_number=round_number,
        timestamp=timestamp,
        country=country,
        result=result,
        derived_refresh=derived_refresh,
        excel_path=excel_path,
        report_path=report_path,
        improvements=improvements,
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_report(summary), encoding="utf-8")
    (output_dir / "latest_report.md").write_text(
        report_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    repository.add_output_artifact(result.scan_run.run_id, "json_summary", str(summary_path))
    repository.add_output_artifact(result.scan_run.run_id, "markdown_report", str(report_path))

    _save_state(
        state_path,
        _next_state(
            state=state,
            registry_countries=registry_countries,
            round_number=round_number,
            country=country,
            result=result,
            report_path=report_path,
            summary_path=summary_path,
            excel_path=excel_path,
        ),
    )

    return AfricaLoopRound(
        round_number=round_number,
        country=country,
        run_id=str(result.scan_run.run_id),
        candidate_count=result.scan_run.candidate_count,
        review_count=result.scan_run.review_count,
        storage_mode=result.storage_mode,
        report_path=report_path,
        summary_path=summary_path,
        excel_path=excel_path,
        improvements=improvements,
        ui_paths={
            "map": f"/ui/?country={country}",
            "geojson": f"/map/properties?country={country}",
            "scan_run": f"/scan-runs/{result.scan_run.run_id}",
        },
    )


def registry_backed_african_countries(registry: dict[str, Any] | None = None) -> list[str]:
    source_registry = registry or load_source_registry()
    countries = source_registry.get("countries", {})
    backed = []
    for country in AFRICAN_COUNTRIES:
        country_registry = _find_registry(country, countries)
        if country_registry and country_registry.get("candidates"):
            backed.append(country)
    return backed


def _find_registry(country: str, countries: dict[str, Any]) -> dict[str, Any] | None:
    normalized = country.casefold()
    for canonical, registry in countries.items():
        aliases = [canonical, *registry.get("aliases", [])]
        if normalized in {alias.casefold() for alias in aliases}:
            return registry
    return None


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "round_number": 0,
            "registry_country_index": 0,
            "cycles_completed": 0,
            "history": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _country_for_round(state: dict[str, Any], registry_countries: list[str]) -> str:
    index = int(state.get("registry_country_index", 0)) % len(registry_countries)
    return registry_countries[index]


def _next_state(
    state: dict[str, Any],
    registry_countries: list[str],
    round_number: int,
    country: str,
    result,
    report_path: Path,
    summary_path: Path,
    excel_path: Path,
) -> dict[str, Any]:
    next_index = int(state.get("registry_country_index", 0)) + 1
    cycles_completed = int(state.get("cycles_completed", 0))
    if next_index >= len(registry_countries):
        next_index = 0
        cycles_completed += 1

    history = list(state.get("history", []))
    history.append(
        {
            "round_number": round_number,
            "country": country,
            "run_id": str(result.scan_run.run_id),
            "candidate_count": result.scan_run.candidate_count,
            "review_count": result.scan_run.review_count,
            "report_path": str(report_path),
            "summary_path": str(summary_path),
            "excel_path": str(excel_path),
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    return {
        "version": 1,
        "round_number": round_number,
        "registry_country_index": next_index,
        "cycles_completed": cycles_completed,
        "registry_backed_countries": registry_countries,
        "africa_country_count": len(AFRICAN_COUNTRIES),
        "history": history[-200:],
    }


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _round_summary(
    round_number: int,
    timestamp: str,
    country: str,
    result,
    derived_refresh: dict[str, Any],
    excel_path: Path,
    report_path: Path,
    improvements: list[str],
) -> dict[str, Any]:
    packets = []
    for packet in result.packets:
        first_evidence = packet.evidence[0] if packet.evidence else None
        packets.append(
            {
                "property_id": str(packet.entity.property_id),
                "property_name": packet.entity.property_name,
                "country": packet.entity.country,
                "city": packet.entity.city,
                "scene_type": packet.entity.scene_type,
                "coordinate_status": packet.entity.coordinate_status,
                "main_metric": first_evidence.field_value if first_evidence else "",
                "source_count": len({str(evidence.source_url) for evidence in packet.evidence}),
                "evidence_status": str(packet.conclusion.evidence_status),
                "value_class": str(packet.conclusion.value_class),
                "action_class": str(packet.conclusion.action_class),
                "recommended_solution": str(packet.conclusion.recommended_solution),
                "indoor_system_presence": str(packet.build_status.indoor_system_presence),
                "build_evidence_status": str(packet.build_status.build_evidence_status),
                "annual_visits_est": packet.scene.annual_visits_est,
                "busy_hour_traffic_gb": (
                    packet.demand.busy_hour_traffic_gb if packet.demand else None
                ),
                "review_next_actions": [item.next_action for item in packet.review_queue],
                "google_maps_link": packet.entity.google_maps_link,
            }
        )
    return {
        "round_number": round_number,
        "timestamp_utc": timestamp,
        "country": country,
        "run_id": str(result.scan_run.run_id),
        "status": result.scan_run.status,
        "storage_mode": result.storage_mode,
        "candidate_count": result.scan_run.candidate_count,
        "review_count": result.scan_run.review_count,
        "persisted_counts": result.persisted_counts,
        "derived_refresh": derived_refresh,
        "persistent_files": {
            "sqlite_default": "outputs/isite2_dev.db",
            "excel": str(excel_path),
            "report": str(report_path),
        },
        "ui_paths": {
            "map": f"/ui/?country={country}",
            "geojson": f"/map/properties?country={country}",
            "scan_run": f"/scan-runs/{result.scan_run.run_id}",
        },
        "packets": packets,
        "improvement_points": improvements,
    }


def _improvement_points(registry_countries: list[str], packets: list[Any]) -> list[str]:
    unseeded = [country for country in AFRICAN_COUNTRIES if country not in registry_countries]
    points = [
        (
            f"Source registry coverage is {len(registry_countries)}/{len(AFRICAN_COUNTRIES)} "
            "African countries; add vetted seeds next for Nigeria, South Africa, Kenya, Morocco, "
            "Ethiopia, Ghana, Tunisia, Tanzania, Uganda, and Cote d'Ivoire."
        )
    ]
    if unseeded:
        points.append(
            "Do not create map points for unseeded countries until official/map evidence is added; "
            f"next unseeded examples: {', '.join(unseeded[:8])}."
        )
    all_build_unknown = all(
        packet.build_status.build_evidence_status == "Unknown" for packet in packets
    )
    if packets and all_build_unknown:
        points.append(
            "Indoor build-status evidence is still Unknown for this round; add operator "
            "announcement and venue network-upgrade source queries as a separate evidence chain."
        )
    single_source = [
        packet.entity.property_name
        for packet in packets
        if len({str(evidence.source_url) for evidence in packet.evidence}) < 2
    ]
    if single_source:
        points.append(
            "Cross-check evidence before upgrading confidence for: "
            + ", ".join(single_source[:5])
            + "."
        )
    points.append(
        "Persist public-source fetch cache across runs so repeated scans do not refetch unchanged "
        "official pages."
    )
    return points


def _render_report(summary: dict[str, Any]) -> str:
    rows = [
        "| Property | City | Scene | Main Metric | Value | Action | Build | Review Action |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for packet in summary["packets"]:
        review_action = "; ".join(packet["review_next_actions"]) or "-"
        rows.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in [
                    packet["property_name"],
                    packet["city"],
                    packet["scene_type"],
                    packet["main_metric"],
                    packet["value_class"],
                    packet["action_class"],
                    packet["build_evidence_status"],
                    review_action,
                ]
            )
            + " |"
        )

    improvements = "\n".join(f"- {point}" for point in summary["improvement_points"])
    return (
        f"# Africa High-Value Building Scan Round {summary['round_number']}\n\n"
        f"- Country: {summary['country']}\n"
        f"- Run ID: {summary['run_id']}\n"
        f"- Status: {summary['status']}\n"
        f"- Storage mode: {summary['storage_mode']}\n"
        f"- Candidates: {summary['candidate_count']}\n"
        f"- Review items: {summary['review_count']}\n"
        f"- SQLite file: {summary['persistent_files']['sqlite_default']}\n"
        f"- Excel file: {summary['persistent_files']['excel']}\n"
        f"- UI map path: {summary['ui_paths']['map']}\n"
        f"- GeoJSON path: {summary['ui_paths']['geojson']}\n\n"
        "## Candidate Results\n\n"
        + "\n".join(rows)
        + "\n\n"
        "## Improvement Points\n\n"
        f"{improvements}\n"
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return slug or "country"
