"""Decision manifests.

A manifest is the reproducibility contract. It records the exact inputs,
policies, seeds, and engine version behind a comparison so another machine can
recreate the same numbers. Replaying a manifest recomputes the metrics and
compares them against what was recorded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forklab_domain import ENGINE_VERSION, Scenario, simulate_replications
from forklab_eval import ComparisonReport, compare

MANIFEST_VERSION = "1"


def build_manifest(
    baseline: Scenario,
    candidate: Scenario,
    report: ComparisonReport,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "baseline": {
            "scenario_id": baseline.scenario_id,
            "label": baseline.label,
            "input_digest": baseline.input_digest(),
            "policy": baseline.policy.model_dump(mode="json"),
            "policy_digest": baseline.policy.digest(),
        },
        "candidate": {
            "scenario_id": candidate.scenario_id,
            "label": candidate.label,
            "input_digest": candidate.input_digest(),
            "policy": candidate.policy.model_dump(mode="json"),
            "policy_digest": candidate.policy.digest(),
        },
        "workload": {
            "orders": len(baseline.orders),
            "skus": len(baseline.inventory),
            "replenishments": len(baseline.replenishments),
        },
        "facility": baseline.facility.model_dump(mode="json"),
        "seeds": report.seeds,
        "replications": report.replications,
        "confidence": report.confidence,
        "bootstrap_samples": report.bootstrap_samples,
        "bootstrap_seed": report.bootstrap_seed,
        "metrics": [m.model_dump(mode="json") for m in report.metrics],
        "notes": report.notes,
        "limitations": limitations
        or [
            "Results come from a synthetic process model, not from observed facility history.",
            "Processing time distributions are estimated and have not been calibrated.",
            "A simulated difference is evidence about the model, not a guarantee about the site.",
        ],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def replay(
    manifest: dict[str, Any],
    baseline: Scenario,
    candidate: Scenario,
) -> tuple[bool, list[str]]:
    """Recompute a manifest's comparison and report any divergence."""
    problems: list[str] = []

    if manifest["engine_version"] != ENGINE_VERSION:
        problems.append(
            f"engine version differs: manifest {manifest['engine_version']}, "
            f"running {ENGINE_VERSION}"
        )
    if manifest["baseline"]["input_digest"] != baseline.input_digest():
        problems.append("baseline input digest differs from the manifest")
    if manifest["candidate"]["input_digest"] != candidate.input_digest():
        problems.append("candidate input digest differs from the manifest")

    seeds = manifest["seeds"]
    report = compare(
        baseline,
        simulate_replications(baseline, seeds),
        candidate,
        simulate_replications(candidate, seeds),
        confidence=manifest["confidence"],
        bootstrap_samples=manifest["bootstrap_samples"],
        bootstrap_seed=manifest["bootstrap_seed"],
    )

    recorded = {m["metric"]: m for m in manifest["metrics"]}
    for metric in report.metrics:
        before = recorded.get(metric.metric)
        if before is None:
            problems.append(f"{metric.metric}: absent from the manifest")
            continue
        for field in ("baseline_mean", "candidate_mean", "mean_difference", "ci_low", "ci_high"):
            if abs(float(before[field]) - float(getattr(metric, field))) > 1e-9:
                problems.append(
                    f"{metric.metric}.{field}: manifest {before[field]}, "
                    f"replay {getattr(metric, field)}"
                )
        if before["verdict"] != metric.verdict:
            problems.append(
                f"{metric.metric}.verdict: manifest {before['verdict']}, replay {metric.verdict}"
            )

    return (not problems, problems)
