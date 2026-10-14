import { OUTCOME_LABEL, OUTCOME_TONE } from "@/lib/format";
import type { Outcome } from "@/lib/types";
import s from "./ui.module.css";

export function OutcomeChip({ outcome, count }: { outcome: Outcome; count?: number }) {
  return (
    <span className={`${s.chip} ${s[OUTCOME_TONE[outcome]]}`}>
      <span className={s.chipLabel}>
        {OUTCOME_LABEL[outcome]}
        {count !== undefined ? <span className="num"> {count}</span> : null}
      </span>
    </span>
  );
}
