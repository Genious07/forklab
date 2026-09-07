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

export function ComparisonTable({ report }: { report: ComparisonReport | null }) {
  if (report === null) {
    return (
      <div className="table-scroll">
        <table className="data">
          <caption>No comparison has been run yet.</caption>
          <thead>
            <tr>
              <th scope="col">Outcome</th>
              <th scope="col" className="num">Baseline</th>
              <th scope="col" className="num">Candidate</th>
              <th scope="col" className="num">Difference</th>
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
    <div className="table-scroll">
      <table className="data">
        <caption>
          {report.replications} paired replications on the same seed set, {report.confidence * 100}
          {"% bootstrap interval on the paired difference. "}
          An interval that contains zero is reported as inconclusive.
        </caption>
        <thead>
          <tr>
            <th scope="col">Outcome</th>
            <th scope="col">Direction</th>
            <th scope="col" className="num">Baseline</th>
            <th scope="col" className="num">Candidate</th>
            <th scope="col" className="num">Difference</th>
            <th scope="col" className="num">Interval</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {unique(report).map((metric) => (
            <tr key={metric.metric}>
              <th scope="row" style={{ fontWeight: 400 }}>{metric.label}</th>
              <td className="footnote">{DIRECTION_TEXT[metric.direction]}</td>
              <td className="num">{metric.baseline_mean.toFixed(4)}</td>
              <td className="num">{metric.candidate_mean.toFixed(4)}</td>
              <td className="num">{signed(metric.mean_difference)}</td>
              <td className="num footnote">
                [{signed(metric.ci_low)}, {signed(metric.ci_high)}]
              </td>
              <td>
                <span className={`verdict ${metric.verdict}`}>{metric.verdict}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
