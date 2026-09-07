"""Paired comparison between a baseline and a candidate scenario.

Two rules drive this module:

1. Comparisons are paired on the seed. Replication i of the baseline and
   replication i of the candidate see the same sampled workload variability,
   so the difference isolates the policy rather than the noise.
2. A point estimate is never enough to call a winner. Every metric reports a
   bootstrap interval on the paired difference and a verdict that says
   "inconclusive" when that interval contains zero.
"""

from __future__ import annotations

import random
from typing import Literal

from forklab_domain.events import RunResult
from forklab_domain.models import Scenario
from pydantic import BaseModel

from .metrics import RunMetrics, run_metrics

Direction = Literal["lower_is_better", "higher_is_better", "neutral"]

METRIC_DIRECTIONS: dict[str, Direction] = {
    "late_proportion": "lower_is_better",
    "late_count": "lower_is_better",
    "mean_wait_minutes": "lower_is_better",
    "p90_wait_minutes": "lower_is_better",
    "mean_cycle_minutes": "lower_is_better",
    "overtime_minutes": "lower_is_better",
    "unfulfilled_count": "lower_is_better",
    "missed_cutoff_count": "lower_is_better",
    "throughput_orders": "higher_is_better",
    "dispatched_count": "higher_is_better",
}

METRIC_LABELS: dict[str, str] = {
    "late_proportion": "Late orders (share)",
    "late_count": "Late orders (count)",
    "mean_wait_minutes": "Mean wait before picking",
    "p90_wait_minutes": "P90 wait before picking",
    "mean_cycle_minutes": "Mean order cycle time",
    "overtime_minutes": "Overtime worked",
    "unfulfilled_count": "Unfulfilled orders",
    "missed_cutoff_count": "Missed dispatch cutoff",
    "throughput_orders": "Orders dispatched",
    "dispatched_count": "Orders dispatched",
}


class MetricComparison(BaseModel):
    metric: str
    label: str
    direction: Direction
    baseline_mean: float
    candidate_mean: float
    mean_difference: float
    ci_low: float
    ci_high: float
    verdict: Literal["improvement", "regression", "inconclusive"]

    @property
    def interval_contains_zero(self) -> bool:
        return self.ci_low <= 0.0 <= self.ci_high


class ComparisonReport(BaseModel):
    baseline_scenario_id: str
    candidate_scenario_id: str
    baseline_policy_digest: str
    candidate_policy_digest: str
    replications: int
    seeds: list[int]
    confidence: float
    bootstrap_samples: int
    bootstrap_seed: int
    engine_version: str
    metrics: list[MetricComparison]
    baseline_metrics: list[RunMetrics]
    candidate_metrics: list[RunMetrics]
    notes: list[str]

    def headline(self) -> list[MetricComparison]:
        wanted = ("late_proportion", "overtime_minutes", "p90_wait_minutes", "throughput_orders")
        order = {name: index for index, name in enumerate(wanted)}
        return sorted(
            [m for m in self.metrics if m.metric in wanted],
            key=lambda m: order[m.metric],
        )


def _bootstrap_interval(
    differences: list[float],
    confidence: float,
    samples: int,
    seed: int,
) -> tuple[float, float]:
    """Percentile bootstrap over the paired differences."""
    if not differences:
        return (0.0, 0.0)
    if len(differences) == 1:
        return (differences[0], differences[0])

    rng = random.Random(seed)
    n = len(differences)
    means: list[float] = []
    for _ in range(samples):
        resample = [differences[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    tail = (1.0 - confidence) / 2.0
    low_index = max(0, min(samples - 1, int(tail * samples)))
    high_index = max(0, min(samples - 1, int((1.0 - tail) * samples) - 1))
    return (means[low_index], means[high_index])


def _verdict(direction: Direction, ci_low: float, ci_high: float) -> str:
    if ci_low <= 0.0 <= ci_high or direction == "neutral":
        return "inconclusive"
    improving = ci_high < 0.0 if direction == "lower_is_better" else ci_low > 0.0
    return "improvement" if improving else "regression"


def compare(
    baseline_scenario: Scenario,
    baseline_runs: list[RunResult],
    candidate_scenario: Scenario,
    candidate_runs: list[RunResult],
    confidence: float = 0.95,
    bootstrap_samples: int = 2000,
    bootstrap_seed: int = 7,
) -> ComparisonReport:
    """Compare two sets of replications that share a seed set."""
    baseline_by_seed = {run.seed: run for run in baseline_runs}
    candidate_by_seed = {run.seed: run for run in candidate_runs}
    seeds = sorted(set(baseline_by_seed) & set(candidate_by_seed))

    notes: list[str] = []
    if not seeds:
        raise ValueError("baseline and candidate share no seeds, so no paired comparison exists")
    dropped = (set(baseline_by_seed) | set(candidate_by_seed)) - set(seeds)
    if dropped:
        notes.append(f"{len(dropped)} unpaired replications were excluded: seeds {sorted(dropped)}")
    if len(seeds) < 30:
        notes.append(
            f"{len(seeds)} paired replications is below the 30 replication guidance, "
            "so intervals are wide and the comparison should be treated as exploratory"
        )
    if baseline_scenario.input_digest() == candidate_scenario.input_digest():
        notes.append("baseline and candidate have identical inputs, so no difference is expected")

    baseline_metrics = [run_metrics(baseline_scenario, baseline_by_seed[s]) for s in seeds]
    candidate_metrics = [run_metrics(candidate_scenario, candidate_by_seed[s]) for s in seeds]

    comparisons: list[MetricComparison] = []
    for metric, direction in METRIC_DIRECTIONS.items():
        base_values = [float(getattr(m, metric)) for m in baseline_metrics]
        cand_values = [float(getattr(m, metric)) for m in candidate_metrics]
        differences = [c - b for b, c in zip(base_values, cand_values, strict=True)]
        ci_low, ci_high = _bootstrap_interval(
            differences, confidence, bootstrap_samples, bootstrap_seed
        )
        comparisons.append(
            MetricComparison(
                metric=metric,
                label=METRIC_LABELS[metric],
                direction=direction,
                baseline_mean=round(sum(base_values) / len(base_values), 4),
                candidate_mean=round(sum(cand_values) / len(cand_values), 4),
                mean_difference=round(sum(differences) / len(differences), 4),
                ci_low=round(ci_low, 4),
                ci_high=round(ci_high, 4),
                verdict=_verdict(direction, ci_low, ci_high),
            )
        )

    return ComparisonReport(
        baseline_scenario_id=baseline_scenario.scenario_id,
        candidate_scenario_id=candidate_scenario.scenario_id,
        baseline_policy_digest=baseline_scenario.policy.digest(),
        candidate_policy_digest=candidate_scenario.policy.digest(),
        replications=len(seeds),
        seeds=seeds,
        confidence=confidence,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
        engine_version=baseline_runs[0].engine_version,
        metrics=comparisons,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        notes=notes,
    )
