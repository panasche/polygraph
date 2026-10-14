import type { Coverage } from "@/lib/types";
import s from "./dashboard.module.css";

/** "18 of 22 pairs tested", a segmented bar, and the riskiest untested pairs first. */
export function CoverageSummary({ coverage, limit = 6 }: { coverage: Coverage; limit?: number }) {
  const untested = coverage.pairs
    .filter((p) => p.runs_done < p.runs_required)
    .sort(
      (a, b) =>
        (a.priority === "top" ? 0 : 1) - (b.priority === "top" ? 0 : 1) ||
        a.runs_done - b.runs_done ||
        a.tool.localeCompare(b.tool),
    );
  const ratio = coverage.applicable ? coverage.covered / coverage.applicable : 0;
  const segments = Math.min(coverage.applicable, 60);
  const filled = Math.round(ratio * segments);

  return (
    <div className={s.coverage}>
      <p className={s.coverageHead}>
        <span className={`num ${s.coverageBig}`}>
          {coverage.covered} of {coverage.applicable}
        </span>
        <span className={s.coverageLabel}>tool and fault pairs tested</span>
      </p>
      {segments > 0 ? (
        <div
          className={s.segments}
          role="img"
          aria-label={`${coverage.covered} of ${coverage.applicable} pairs tested, ${Math.round(ratio * 100)}%`}
        >
          {Array.from({ length: segments }, (_, i) => (
            <span key={i} className={i < filled ? s.segOn : s.segOff} />
          ))}
        </div>
      ) : null}
      {untested.length > 0 ? (
        <div className={s.untested}>
          <h3>Riskiest untested pairs</h3>
          <ul>
            {untested.slice(0, limit).map((p) => (
              <li key={`${p.server}/${p.tool}/${p.fault}`}>
                <code>
                  {p.tool} × {p.fault}
                </code>
                <span className={s.faint}>
                  {p.priority === "top" ? "Top priority. " : ""}
                  {p.runs_done} of {p.runs_required} runs
                </span>
              </li>
            ))}
          </ul>
          {untested.length > limit ? <p className={s.faint}>And {untested.length - limit} more.</p> : null}
        </div>
      ) : coverage.applicable > 0 ? (
        <p className={s.allCovered}>Every pair that applies was tested.</p>
      ) : null}
    </div>
  );
}
