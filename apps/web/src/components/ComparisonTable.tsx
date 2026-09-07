import type { ComparisonReport } from "../api";
import { signed } from "../api";

/* Improvement is never carried by colour alone. Every row states a signed
   difference, an interval, and a word. */

const DIRECTION_TEXT: Record<string, string> = {
  lower_is_better: "lower is better",
  higher_is_better: "higher is better",
  neutral: "no preferred direction",
};

function unique(report: ComparisonReport) {
  const seen = new Set<string>();
  return report.metrics.filter((metric) => {
    if (seen.has(metric.label)) return false;
    seen.add(metric.label);
    return true;
  });
}

export function ComparisonTable({
  report,
}: {
  report: ComparisonReport | null;
}) {
  if (report === null) {
    return (
      <div className="table-scroll">
        <table className="data">
          <caption>No comparison has been run yet.</caption>
          <thead>
            <tr>
              <th scope="col">Outcome</th>
              <th scope="col" className="num">
                Baseline
              </th>
              <th scope="col" className="num">
                Candidate
              </th>
              <th scope="col" className="num">
                Difference
              </th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {["Late orders", "Overtime"].map((name) => (
              <tr key={name}>
                <td>{name}</td>
                <td className="num not-run">Not run</td>
                <td className="num not-run">Not run</td>
                <td className="num not-run">Not measured</td>
                <td className="not-run">Run comparison</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div>
      <div className="effect-strip">
        {unique(report)
          .filter((m) =>
            [
              "late_proportion",
              "p90_wait_minutes",
              "throughput_orders",
            ].includes(m.metric),
          )
          .map((m) => {
            const span =
              Math.max(Math.abs(m.ci_low), Math.abs(m.ci_high), 0.001) * 1.25;
            const x = (v: number) => 100 + (v / span) * 85;
            const percentage = m.metric === "late_proportion";
            return (
              <div className="effect" key={m.metric}>
                <span>{m.label}</span>
                <strong>
                  {signed(m.mean_difference * (percentage ? 100 : 1), 2)}{" "}
                  {percentage
                    ? "pp"
                    : m.metric.includes("wait")
                      ? "min"
                      : "orders"}
                </strong>
                <svg
                  viewBox="0 0 200 35"
                  role="img"
                  aria-label={`Paired difference ${m.mean_difference}, interval ${m.ci_low} to ${m.ci_high}`}
                >
                  <path d="M15 18h170" stroke="#bacbd5" />
                  <path d="M100 2v31" stroke="#4a6274" strokeDasharray="2 3" />
                  <path
                    d={`M${x(m.ci_low)} 18H${x(m.ci_high)}`}
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <circle
                    cx={x(m.mean_difference)}
                    cy="18"
                    r="5"
                    fill="currentColor"
                  />
                </svg>
                <small>Candidate minus baseline; dashed line = zero</small>
                <span className={`verdict ${m.verdict}`}>{m.verdict}</span>
              </div>
            );
          })}
      </div>
      <div className="table-scroll">
        <table className="data">
          <caption>
            {report.replications} paired replications on the same seed set,{" "}
            {report.confidence * 100}
            {"% bootstrap interval on the paired difference. "}
            An interval that contains zero is reported as inconclusive.
          </caption>
          <thead>
            <tr>
              <th scope="col">Outcome</th>
              <th scope="col">Direction</th>
              <th scope="col" className="num">
                Baseline
              </th>
              <th scope="col" className="num">
                Candidate
              </th>
              <th scope="col" className="num">
                Difference
              </th>
              <th scope="col" className="num">
                Interval
              </th>
              <th scope="col">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {unique(report).map((metric) => (
              <tr key={metric.metric}>
                <th scope="row" style={{ fontWeight: 400 }}>
                  {metric.label}
                </th>
                <td className="footnote">{DIRECTION_TEXT[metric.direction]}</td>
                <td className="num">{metric.baseline_mean.toFixed(4)}</td>
                <td className="num">{metric.candidate_mean.toFixed(4)}</td>
                <td className="num">{signed(metric.mean_difference)}</td>
                <td className="num footnote">
                  [{signed(metric.ci_low)}, {signed(metric.ci_high)}]
                </td>
                <td>
                  <span className={`verdict ${metric.verdict}`}>
                    {metric.verdict}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {report.notes.length > 0 && (
        <ul className="footnote">
          {report.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
