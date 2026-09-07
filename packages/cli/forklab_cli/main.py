"""ForkLab command line.

Every subcommand works offline against synthetic fixtures and needs no API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forklab_domain import (
    ENGINE_VERSION,
    PolicyVersion,
    Scenario,
    build_report,
    check_reproducible,
    check_run,
    demo_scenario,
    describe_policy,
    fork,
    hand_solvable_scenario,
    read_inventory,
    read_orders,
    read_replenishments,
    scenario_to_csvs,
    simulate,
    simulate_replications,
)
from forklab_eval import compare, run_metrics

from .manifest import build_manifest, replay, write_manifest

STRATEGIES = ("fifo", "edf", "batch_by_sku")


def build_policy(args: argparse.Namespace, name: str) -> PolicyVersion:
    return PolicyVersion(
        name=name,
        priority_strategy=args.strategy,
        batching_threshold=args.batch_threshold,
        dispatch_cutoff_minute=args.cutoff,
        allowed_overtime_minutes=args.overtime,
        tie_breaking_rule=args.ties,
    )


def load_scenario(args: argparse.Namespace) -> Scenario:
    """Load from a CSV directory when given, otherwise use the synthetic demo."""
    if args.data is None:
        return demo_scenario(seed=args.data_seed, order_count=args.orders)

    directory = Path(args.data)
    orders_text = (directory / "orders.csv").read_text(encoding="utf-8")
    inventory_text = (directory / "inventory.csv").read_text(encoding="utf-8")
    replen_path = directory / "replenishments.csv"

    orders, order_issues = read_orders(orders_text)
    inventory, inventory_issues = read_inventory(inventory_text)
    replenishments: list = []
    replen_issues: list = []
    if replen_path.exists():
        replenishments, replen_issues = read_replenishments(replen_path.read_text(encoding="utf-8"))

    report = build_report(orders, inventory, order_issues + inventory_issues + replen_issues)
    if not report.is_clean:
        print(f"Import rejected {report.rejected_rows} rows.", file=sys.stderr)
        for issue in report.issues[:10]:
            print(f"  line {issue.line_number}: {issue.reason}", file=sys.stderr)
        if report.missing_skus:
            print(
                f"  {len(report.missing_skus)} SKUs referenced by orders are missing "
                f"from inventory: {', '.join(report.missing_skus[:8])}",
                file=sys.stderr,
            )
        raise SystemExit(2)

    return Scenario(
        scenario_id=directory.name,
        label=f"Imported from {directory}",
        orders=orders,
        inventory=inventory,
        replenishments=replenishments,
    )


def print_metric_table(report) -> None:
    header = (
        f"{'Outcome':30s} {'Baseline':>11s} {'Candidate':>11s} "
        f"{'Difference':>12s}  {'95% interval':>22s}  Verdict"
    )
    print(header)
    print("-" * len(header))
    seen: set[str] = set()
    for metric in report.metrics:
        if metric.label in seen:
            continue
        seen.add(metric.label)
        interval = f"[{metric.ci_low:+.4f}, {metric.ci_high:+.4f}]"
        print(
            f"{metric.label:30s} {metric.baseline_mean:11.4f} {metric.candidate_mean:11.4f} "
            f"{metric.mean_difference:+12.4f}  {interval:>22s}  {metric.verdict}"
        )
    print()
    for metric in report.metrics:
        if metric.metric == "late_proportion":
            arrow = (
                "lower is better" if metric.direction == "lower_is_better" else "higher is better"
            )
            print(f"Late orders direction: {arrow}.")
            break
    for note in report.notes:
        print(f"Note: {note}")


# --------------------------------------------------------------------- commands


def cmd_run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args)
    scenario = fork(scenario, scenario.scenario_id, scenario.label, build_policy(args, "run"))
    seeds = list(range(1, args.replications + 1))

    print(f"Scenario     {scenario.label}")
    print(f"Orders       {len(scenario.orders)}  across {len(scenario.inventory)} SKUs")
    print(f"Policy       {describe_policy(scenario.policy)}")
    print(f"Digest       input={scenario.input_digest()} policy={scenario.policy.digest()}")
    print(f"Engine       {ENGINE_VERSION}")
    print()

    runs = simulate_replications(scenario, seeds)
    failures = check_run(scenario, runs[0])
    if failures:
        print("INVARIANT FAILURES:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    rows = [run_metrics(scenario, run) for run in runs]
    mean = lambda field: sum(getattr(r, field) for r in rows) / len(rows)  # noqa: E731

    print(f"{args.replications} replications, invariants clean")
    print(f"  late orders        {mean('late_proportion'):.4f}  ({mean('late_count'):.1f} orders)")
    print(f"  missed cutoff      {mean('missed_cutoff_count'):.1f}")
    print(f"  unfulfilled        {mean('unfulfilled_count'):.1f}")
    print(f"  blocked on stock   {mean('blocked_on_stock_count'):.1f}")
    print(f"  mean wait          {mean('mean_wait_minutes'):.2f} min")
    print(f"  p90 wait           {mean('p90_wait_minutes'):.2f} min")
    print(f"  mean cycle time    {mean('mean_cycle_minutes'):.2f} min")
    print(f"  overtime           {mean('overtime_minutes'):.2f} min")
    print()
    print("  late share by service class")
    for name in rows[0].late_proportion_by_class:
        share = sum(r.late_proportion_by_class.get(name, 0.0) for r in rows) / len(rows)
        print(f"    {name:10s} {share:.4f}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    scenario = load_scenario(args)
    baseline = fork(
        scenario,
        f"{scenario.scenario_id}-baseline",
        "Baseline",
        PolicyVersion(
            name="baseline",
            priority_strategy=args.strategy,
            batching_threshold=args.batch_threshold,
            dispatch_cutoff_minute=args.cutoff,
            allowed_overtime_minutes=args.overtime,
            tie_breaking_rule=args.ties,
        ),
    )
    candidate = fork(
        scenario,
        f"{scenario.scenario_id}-candidate",
        "Candidate",
        PolicyVersion(
            name="candidate",
            priority_strategy=args.candidate_strategy,
            batching_threshold=args.candidate_batch_threshold,
            dispatch_cutoff_minute=args.candidate_cutoff,
            allowed_overtime_minutes=args.candidate_overtime,
            tie_breaking_rule=args.candidate_ties,
        ),
    )

    seeds = list(range(1, args.replications + 1))
    print(f"Baseline    {describe_policy(baseline.policy)}")
    print(f"Candidate   {describe_policy(candidate.policy)}")
    print(f"Workload    {len(scenario.orders)} orders, {args.replications} paired replications")
    print(f"Engine      {ENGINE_VERSION}")
    print()

    baseline_runs = simulate_replications(baseline, seeds)
    candidate_runs = simulate_replications(candidate, seeds)

    for label, scen, runs in (
        ("baseline", baseline, baseline_runs),
        ("candidate", candidate, candidate_runs),
    ):
        failures = check_run(scen, runs[0])
        if failures:
            print(f"INVARIANT FAILURES in {label}:")
            for failure in failures:
                print(f"  {failure}")
            return 1

    report = compare(baseline, baseline_runs, candidate, candidate_runs)
    print_metric_table(report)

    if args.manifest:
        manifest = build_manifest(baseline, candidate, report)
        write_manifest(Path(args.manifest), manifest)
        print()
        print(f"Decision manifest written to {args.manifest}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    print("Hand solvable case")
    hand = hand_solvable_scenario()
    result = simulate(hand, seed=1)
    dispatched = {e.order_id: e.minute for e in result.events if e.event_type.value == "dispatched"}
    expected = {f"H{k:02d}": float(3 * k + 2) for k in range(1, 11)}
    if dispatched != expected:
        print("  FAIL: dispatch times do not match the hand calculation")
        print(f"  expected {expected}")
        print(f"  observed {dispatched}")
        return 1
    print(f"  dispatch times match the hand calculation: {sorted(expected.values())}")

    print("Invariants on the demo day")
    scenario = demo_scenario()
    failures = check_run(scenario, simulate(scenario, seed=1))
    if failures:
        for failure in failures:
            print(f"  FAIL {failure}")
        return 1
    print("  clean: stock conserved, capacity respected, one terminal state per order")

    print("Reproducibility")
    for seed in (1, 2, 3):
        problems = check_reproducible(demo_scenario(order_count=150), seed)
        if problems:
            print(f"  FAIL {problems}")
            return 1
    print("  identical seeds reproduce identical event logs")
    return 0


def cmd_export_fixtures(args: argparse.Namespace) -> int:
    scenario = demo_scenario(seed=args.data_seed, order_count=args.orders)
    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in scenario_to_csvs(scenario).items():
        (directory / name).write_text(text, encoding="utf-8")
        print(f"wrote {directory / name}")
    readme = directory / "README.md"
    readme.write_text(
        "# Demonstration data\n\n"
        "These files are synthetic and generated by "
        "`forklab export-fixtures`. They describe no real facility, customer, "
        "or supplier. Times are minutes from the start of the facility day, "
        "where minute 0 is 08:00 local time.\n",
        encoding="utf-8",
    )
    print(f"wrote {readme}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    scenario = demo_scenario(seed=args.data_seed, order_count=manifest["workload"]["orders"])
    baseline = fork(
        scenario,
        manifest["baseline"]["scenario_id"],
        manifest["baseline"]["label"],
        PolicyVersion(**manifest["baseline"]["policy"]),
    )
    candidate = fork(
        scenario,
        manifest["candidate"]["scenario_id"],
        manifest["candidate"]["label"],
        PolicyVersion(**manifest["candidate"]["policy"]),
    )

    print(f"Replaying {args.manifest}")
    print(f"  engine in manifest {manifest['engine_version']}, running {ENGINE_VERSION}")
    print(f"  {manifest['replications']} paired replications")

    ok, problems = replay(manifest, baseline, candidate)
    if ok:
        print("  every recorded metric reproduced exactly")
        return 0
    print("  divergence found:")
    for problem in problems:
        print(f"    {problem}")
    return 1


def cmd_demo(args: argparse.Namespace) -> int:
    """The five minute walkthrough, end to end, with no configuration."""
    scenario = demo_scenario()
    seeds = list(range(1, 31))

    print("=" * 78)
    print("ForkLab demonstration: should the north warehouse switch dispatch priority?")
    print("=" * 78)
    print()
    print(f"Synthetic day: {len(scenario.orders)} orders across {len(scenario.inventory)} SKUs")
    print(
        f"Facility: {scenario.facility.pickers} pickers, "
        f"{scenario.facility.packing_stations} packing stations, "
        f"shift ends at minute {scenario.facility.shift.shift_end_minute:.0f}"
    )
    print(
        "Assumptions: " + ", ".join(f"{k} ({v})" for k, v in scenario.facility.provenance.items())
    )
    print()

    baseline_runs = simulate_replications(scenario, seeds)
    failures = check_run(scenario, baseline_runs[0])
    print(f"Invariants on the baseline run: {'clean' if not failures else failures}")
    if failures:
        return 1

    baseline_metrics = run_metrics(scenario, baseline_runs[0])
    print()
    print("Baseline, first in first out:")
    print(f"  late orders {baseline_metrics.late_count} of {baseline_metrics.order_count}")
    for name, share in baseline_metrics.late_proportion_by_class.items():
        print(f"    {name:10s} {share:.1%} late")
    print()

    for label, policy in (
        (
            "Earliest deadline first",
            PolicyVersion(name="edf", priority_strategy="edf", dispatch_cutoff_minute=540.0),
        ),
        (
            "Dispatch cutoff moved one hour earlier",
            PolicyVersion(name="cutoff480", priority_strategy="fifo", dispatch_cutoff_minute=480.0),
        ),
    ):
        candidate = fork(scenario, f"demo-{policy.name}", label, policy)
        report = compare(
            scenario, baseline_runs, candidate, simulate_replications(candidate, seeds)
        )
        print("-" * 78)
        print(f"Fork: {label}")
        print("-" * 78)
        print_metric_table(report)
        print()

    print("Every number above came from running the simulator in this process.")
    print("Run `forklab verify` to check the invariants and the hand solvable case.")
    return 0


# ----------------------------------------------------------------------- parser


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", choices=STRATEGIES, default="fifo")
    parser.add_argument("--batch-threshold", type=int, default=1, dest="batch_threshold")
    parser.add_argument("--cutoff", type=float, default=540.0, help="dispatch cutoff, minutes")
    parser.add_argument("--overtime", type=float, default=0.0, help="allowed overtime, minutes")
    parser.add_argument(
        "--ties",
        choices=("order_id", "smallest_quantity", "largest_quantity"),
        default="order_id",
    )


def add_workload_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data", default=None, help="directory holding orders.csv and inventory.csv"
    )
    parser.add_argument("--data-seed", type=int, default=20260907, dest="data_seed")
    parser.add_argument("--orders", type=int, default=650)
    parser.add_argument("--replications", type=int, default=30)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forklab",
        description="Rehearse a warehouse decision before changing the warehouse.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the full walkthrough with synthetic data")
    demo.set_defaults(func=cmd_demo)

    verify = sub.add_parser("verify", help="check invariants, the hand case, and reproducibility")
    verify.set_defaults(func=cmd_verify)

    run = sub.add_parser("run", help="run one policy and report its metrics")
    add_workload_arguments(run)
    add_policy_arguments(run)
    run.set_defaults(func=cmd_run)

    comp = sub.add_parser("compare", help="compare a baseline policy against a candidate")
    add_workload_arguments(comp)
    add_policy_arguments(comp)
    comp.add_argument("--candidate-strategy", choices=STRATEGIES, default="edf")
    comp.add_argument("--candidate-batch-threshold", type=int, default=1)
    comp.add_argument("--candidate-cutoff", type=float, default=540.0)
    comp.add_argument("--candidate-overtime", type=float, default=0.0)
    comp.add_argument(
        "--candidate-ties",
        choices=("order_id", "smallest_quantity", "largest_quantity"),
        default="order_id",
    )
    comp.add_argument("--manifest", default=None, help="write a decision manifest to this path")
    comp.set_defaults(func=cmd_compare)

    export = sub.add_parser("export-fixtures", help="write the synthetic day out as CSV")
    export.add_argument("--out", default="fixtures/demo")
    export.add_argument("--data-seed", type=int, default=20260907, dest="data_seed")
    export.add_argument("--orders", type=int, default=650)
    export.set_defaults(func=cmd_export_fixtures)

    rep = sub.add_parser("replay", help="recompute a decision manifest and check it reproduces")
    rep.add_argument("manifest")
    rep.add_argument("--data-seed", type=int, default=20260907, dest="data_seed")
    rep.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
