import { OUTCOME_LABEL, OUTCOME_TONE, OUTCOMES } from "@/lib/format";
import type { Outcome } from "@/lib/types";
import s from "./charts.module.css";

/** Stacked bar of outcome buckets, in a fixed order so reports compare at a glance. */
export function OutcomeBar({ outcomes, total }: { outcomes: Partial<Record<Outcome, number>>; total?: number }) {
  const sum = total ?? OUTCOMES.reduce((a, o) => a + (outcomes[o] ?? 0), 0);
  return (
    <div className={s.outcomes}>
      <div className={s.outcomeBar} role="img" aria-label={describe(outcomes, sum)}>
        {sum === 0 ? <span className={s.outcomeEmpty} /> : null}
        {OUTCOMES.map((o) => {
          const n = outcomes[o] ?? 0;
          if (!n) return null;
          return (
            <span
              key={o}
              className={`${s.outcomeSeg} ${s[`seg_${o}`]}`}
              style={{ flexGrow: n }}
              title={`${OUTCOME_LABEL[o]}: ${n}`}
            />
          );
        })}
      </div>
      <ul className={s.outcomeLegend}>
        {OUTCOMES.map((o) => (
          <li key={o} className={(outcomes[o] ?? 0) === 0 ? s.legendZero : ""}>
            <span className={`${s.legendSwatch} ${s[`seg_${o}`]}`} aria-hidden="true" />
            <span>{OUTCOME_LABEL[o]}</span>
            <span className={`${s.legendCount} num ${OUTCOME_TONE[o] === "bad" && (outcomes[o] ?? 0) > 0 ? s.valueBad : ""}`}>
              {outcomes[o] ?? 0}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function describe(outcomes: Partial<Record<Outcome, number>>, sum: number) {
  if (sum === 0) return "No runs";
  return OUTCOMES.filter((o) => (outcomes[o] ?? 0) > 0)
    .map((o) => `${OUTCOME_LABEL[o]} ${outcomes[o]}`)
    .join(", ");
}
