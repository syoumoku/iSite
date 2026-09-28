from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from isite2.rules.traffic_calibration import (  # noqa: E402
    TrafficBenchmarkSample,
    evaluate_traffic_rollout,
)
from isite2.db.session import create_engine_for_url  # noqa: E402
from isite2.growth.derived_refresh import (  # noqa: E402
    _annual_visits_from_metric,
    _evidence_pool,
    _latest_targets,
    _metric_candidates,
)
from isite2.growth.traffic_refresh import _traffic_metrics  # noqa: E402
from isite2.rules.traffic import (  # noqa: E402
    DIRECT_VISIT_KEYS,
    estimate_traffic_v2,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate scene-level Traffic V2 rollout gates from retained benchmarks."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("ISITE2_DATABASE_URL"),
    )
    parser.add_argument("--country", default="Brazil")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--samples-output",
        type=Path,
        help="Retain generated counterfactual benchmark samples and audit context.",
    )
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--required-improvement", type=float, default=0.20)
    args = parser.parse_args()
    inventory: dict = {}
    if args.input:
        samples = [
            TrafficBenchmarkSample(**item)
            for item in _load_records(args.input)
        ]
    else:
        samples, inventory = _database_benchmarks(
            str(args.database_url),
            country=args.country,
        )
    gates = evaluate_traffic_rollout(
        samples,
        minimum_sample_count=args.minimum_samples,
        required_relative_improvement=args.required_improvement,
    )
    report = {
        "mode": "traffic_v2_rollout_qa",
        "sample_count": len(samples),
        "country": args.country if args.database_url else None,
        "inventory": inventory,
        "gates": [gate.as_dict() for gate in gates],
        "rollout_allowed": bool(gates) and all(
            gate.rollout_allowed for gate in gates
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    if args.samples_output:
        args.samples_output.parent.mkdir(parents=True, exist_ok=True)
        args.samples_output.write_text(
            json.dumps(
                {
                    "country": args.country,
                    "samples": [sample.__dict__ for sample in samples],
                    "inventory": inventory,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(text, end="")
    return 0 if report["rollout_allowed"] else 2


def _load_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.casefold() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    return payload if isinstance(payload, list) else list(payload.get("samples") or [])


def _database_benchmarks(
    database_url: str,
    *,
    country: str,
) -> tuple[list[TrafficBenchmarkSample], dict]:
    engine = create_engine_for_url(database_url)
    with engine.begin() as connection:
        targets = _latest_targets(connection, scan_run_id=None)
        normalized_country = country.strip().casefold()
        targets = [
            target
            for target in targets
            if str(target["country"]).strip().casefold() == normalized_country
        ]
        evidence = _evidence_pool(
            connection,
            [target["property_id"] for target in targets],
        )

    samples: list[TrafficBenchmarkSample] = []
    counts: Counter[str] = Counter()
    scene_counts: dict[str, Counter[str]] = {}
    audit_rows: list[dict] = []
    calibration_gap_rows: list[dict] = []
    for target in targets:
        scene_type = str(target["scene_type"])
        scene_counter = scene_counts.setdefault(scene_type, Counter())
        evidence_rows = evidence.get(target["property_id"], [])
        all_metrics = _traffic_metrics(scene_type, evidence_rows)
        observed = estimate_traffic_v2(scene_type, all_metrics)
        scene_counter[f"observed_{observed.estimate_method}"] += 1
        counts[f"observed_{observed.estimate_method}"] += 1
        if observed.estimate_method != "direct_annual":
            if (
                observed.estimate_method in {"scene_proxy", "scene_components"}
                and observed.annual_visits_p50 is not None
            ):
                calibration_gap_rows.append(
                    {
                        "property_id": target["property_id"],
                        "property_name": target["canonical_name"],
                        "city": target["city"],
                        "scene_type": scene_type,
                        "proxy_method": observed.estimate_method,
                        "proxy_metric": observed.selected_metric_key,
                        "proxy_evidence_ids": observed.selected_evidence_ids,
                        "proxy_annual_visits_p50": observed.annual_visits_p50,
                        "required_evidence": (
                            "Obtain an official or operator-published direct annual "
                            "visitation/footfall figure for calibration."
                        ),
                    }
                )
            continue

        proxy_inputs = [
            metric
            for metric in all_metrics
            if metric.unit not in {"visits/year", "visits/day"}
            and metric.key not in DIRECT_VISIT_KEYS
        ]
        v2_proxy = estimate_traffic_v2(scene_type, proxy_inputs)
        if (
            v2_proxy.estimate_method not in {"scene_proxy", "scene_components"}
            or v2_proxy.annual_visits_p50 is None
        ):
            scene_counter["missing_v2_counterfactual"] += 1
            counts["missing_v2_counterfactual"] += 1
            continue

        v1_candidates = [
            metric
            for metric in _metric_candidates(scene_type, evidence_rows)
            if metric.numeric_unit not in {"visits/year", "visits/day"}
            and metric.field_key not in DIRECT_VISIT_KEYS
        ]
        v1_metric = (
            sorted(v1_candidates, key=lambda item: item.priority)[0]
            if v1_candidates
            else None
        )
        v1_proxy = _annual_visits_from_metric(scene_type, v1_metric)
        if v1_proxy is None:
            scene_counter["missing_v1_counterfactual"] += 1
            counts["missing_v1_counterfactual"] += 1
            continue

        sample = TrafficBenchmarkSample(
            scene_type=scene_type,
            actual_annual_visits=float(observed.annual_visits_p50),
            v1_annual_visits=float(v1_proxy.value),
            v2_annual_visits_p50=float(v2_proxy.annual_visits_p50),
            property_id=str(target["property_id"]),
        )
        samples.append(sample)
        scene_counter["eligible_benchmark"] += 1
        counts["eligible_benchmark"] += 1
        audit_rows.append(
            {
                "property_id": target["property_id"],
                "property_name": target["canonical_name"],
                "scene_type": scene_type,
                "actual_annual_visits": sample.actual_annual_visits,
                "actual_evidence_ids": observed.selected_evidence_ids,
                "v1_annual_visits": sample.v1_annual_visits,
                "v1_proxy_metric": v1_metric.field_key if v1_metric else None,
                "v2_annual_visits_p50": sample.v2_annual_visits_p50,
                "v2_proxy_method": v2_proxy.estimate_method,
                "v2_proxy_metric": v2_proxy.selected_metric_key,
                "v2_proxy_evidence_ids": v2_proxy.selected_evidence_ids,
            }
        )
    return samples, {
        "target_count": len(targets),
        "counts": dict(counts),
        "scene_counts": {
            scene: dict(values)
            for scene, values in sorted(scene_counts.items())
        },
        "audit_rows": audit_rows,
        "calibration_gap_rows": calibration_gap_rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
