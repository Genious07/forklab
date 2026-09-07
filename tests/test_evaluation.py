"""Evaluation tests. The statistics must refuse to overclaim."""

from __future__ import annotations

import pytest
from forklab_domain import PolicyVersion, demo_scenario, fork, simulate_replications
from forklab_eval import compare, outcomes, run_metrics


def test_metrics_come_only_from_the_event_log():
    scenario = demo_scenario(order_count=120)
    run = simulate_replications(scenario, [1])[0]
    metrics = run_metrics(scenario, run)
    rows = outcomes(scenario, run)

    assert metrics.order_count == len(scenario.orders) == len(rows)
    assert metrics.late_count == sum(1 for row in rows if row.is_late)
    terminal = metrics.dispatched_count + metrics.missed_cutoff_count + metrics.unfulfilled_count
    assert terminal == metrics.order_count


def test_comparing_a_scenario_against_itself_is_always_inconclusive():
    """The strongest guard against a false positive."""
    scenario = demo_scenario(order_count=150)
    seeds = list(range(1, 21))
    runs = simulate_replications(scenario, seeds)
    report = compare(scenario, runs, scenario, runs)

    assert all(m.verdict == "inconclusive" for m in report.metrics)
    assert all(m.mean_difference == 0.0 for m in report.metrics)
    assert any("identical inputs" in note for note in report.notes)


def test_low_replication_count_is_flagged():
    scenario = demo_scenario(order_count=80)
    seeds = [1, 2, 3]
    runs = simulate_replications(scenario, seeds)
    candidate = fork(scenario, "c", "EDF", PolicyVersion(priority_strategy="edf"))
    report = compare(scenario, runs, candidate, simulate_replications(candidate, seeds))
    assert any("below the 30 replication guidance" in note for note in report.notes)


def test_unpaired_seeds_are_excluded_and_reported():
    scenario = demo_scenario(order_count=80)
    candidate = fork(scenario, "c", "EDF", PolicyVersion(priority_strategy="edf"))
    baseline_runs = simulate_replications(scenario, [1, 2, 3, 4])
    candidate_runs = simulate_replications(candidate, [1, 2, 3])
    report = compare(scenario, baseline_runs, candidate, candidate_runs)

    assert report.replications == 3
    assert report.seeds == [1, 2, 3]
    assert any("unpaired" in note for note in report.notes)


def test_no_shared_seeds_raises_rather_than_guessing():
    scenario = demo_scenario(order_count=60)
    candidate = fork(scenario, "c", "EDF", PolicyVersion(priority_strategy="edf"))
    with pytest.raises(ValueError, match="share no seeds"):
        compare(
            scenario,
            simulate_replications(scenario, [1, 2]),
            candidate,
            simulate_replications(candidate, [3, 4]),
        )


def test_earliest_deadline_first_reduces_lateness_on_the_demo_day():
    """A directional claim, checked by running the comparison rather than asserting a constant."""
    scenario = demo_scenario()
    seeds = list(range(1, 31))
    baseline_runs = simulate_replications(scenario, seeds)
    candidate = fork(scenario, "demo-edf", "EDF", PolicyVersion(priority_strategy="edf"))
    report = compare(scenario, baseline_runs, candidate, simulate_replications(candidate, seeds))

    late = next(m for m in report.metrics if m.metric == "late_proportion")
    assert late.verdict == "improvement"
    assert late.ci_high < 0.0


def test_direction_is_respected_when_judging_a_verdict():
    """A larger number is not automatically better."""
    scenario = demo_scenario(order_count=300)
    seeds = list(range(1, 21))
    baseline_runs = simulate_replications(scenario, seeds)
    early = fork(
        scenario,
        "early",
        "Earlier cutoff",
        PolicyVersion(priority_strategy="fifo", dispatch_cutoff_minute=420.0),
    )
    report = compare(scenario, baseline_runs, early, simulate_replications(early, seeds))

    dispatched = next(m for m in report.metrics if m.metric == "throughput_orders")
    assert dispatched.direction == "higher_is_better"
    assert dispatched.mean_difference < 0.0
    assert dispatched.verdict == "regression"


def test_bootstrap_interval_is_reproducible():
    scenario = demo_scenario(order_count=100)
    seeds = list(range(1, 11))
    baseline_runs = simulate_replications(scenario, seeds)
    candidate = fork(scenario, "c", "EDF", PolicyVersion(priority_strategy="edf"))
    candidate_runs = simulate_replications(candidate, seeds)

    first = compare(scenario, baseline_runs, candidate, candidate_runs)
    second = compare(scenario, baseline_runs, candidate, candidate_runs)
    assert [m.ci_low for m in first.metrics] == [m.ci_low for m in second.metrics]
    assert [m.ci_high for m in first.metrics] == [m.ci_high for m in second.metrics]


def test_one_pair_cannot_claim_an_improvement():
    baseline = demo_scenario(order_count=120)
    candidate = fork(baseline, "one", "EDF", PolicyVersion(priority_strategy="edf"))
    report = compare(
        baseline,
        simulate_replications(baseline, [1]),
        candidate,
        simulate_replications(candidate, [1]),
    )
    assert all(metric.verdict == "inconclusive" for metric in report.metrics)


def test_different_workloads_cannot_be_a_paired_policy_comparison():
    baseline = demo_scenario(order_count=60)
    candidate = demo_scenario(order_count=61)
    with pytest.raises(ValueError, match="workload"):
        compare(
            baseline,
            simulate_replications(baseline, [1]),
            candidate,
            simulate_replications(candidate, [1]),
        )
