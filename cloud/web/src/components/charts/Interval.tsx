import { ciText, pct } from "@/lib/format";
import type { Rate } from "@/lib/types";
import s from "./charts.module.css";

type Tone = "good" | "bad" | "neutral";

/**
 * A rate drawn as a dot with its 95 % confidence whisker on a 0–max track.
 * Honest by construction: a 0 % from 3 runs shows a wide whisker.
 */
export function IntervalBar({
  rate,
  max = 1,
  tone = "neutral",
  width = 160,
  label,
}: {
  rate: Rate;
  max?: number;
  tone?: Tone;
  width?: number;
  label: string;
}) {
  const h = 18;
  const pad = 5;
  const x = (v: number) => pad + (Math.min(v, max) / max) * (width - pad * 2);
  const has = rate.value !== null && rate.ci_low !== null && rate.ci_high !== null;
  const desc = has
    ? `${label}: ${pct(rate, 1)}, 95% interval ${ciText(rate)}, ${rate.numerator} of ${rate.denominator} runs`
    : `${label}: no runs yet`;
  return (
    <svg
      width={width}
      height={h}
      viewBox={`0 0 ${width} ${h}`}
      className={`${s.interval} ${s[`tone_${tone}`]}`}
      role="img"
      aria-label={desc}
    >
      <line x1={pad} x2={width - pad} y1={h / 2} y2={h / 2} className={s.track} />
      <line x1={pad} x2={pad} y1={h / 2 - 3} y2={h / 2 + 3} className={s.track} />
      <line x1={width - pad} x2={width - pad} y1={h / 2 - 3} y2={h / 2 + 3} className={s.track} />
      {has ? (
        <>
          <line x1={x(rate.ci_low!)} x2={x(rate.ci_high!)} y1={h / 2} y2={h / 2} className={s.whisker} />
          <line x1={x(rate.ci_low!)} x2={x(rate.ci_low!)} y1={h / 2 - 4} y2={h / 2 + 4} className={s.whisker} />
          <line x1={x(rate.ci_high!)} x2={x(rate.ci_high!)} y1={h / 2 - 4} y2={h / 2 + 4} className={s.whisker} />
          <circle cx={x(rate.value!)} cy={h / 2} r={4} className={s.dot} />
        </>
      ) : null}
    </svg>
  );
}

/** One metric row: name, value, interval plot, and the counts behind it. */
export function MetricRow({
  name,
  hint,
  rate,
  tone,
  max = 1,
}: {
  name: string;
  hint?: string;
  rate: Rate;
  tone: Tone;
  max?: number;
}) {
  const empty = rate.denominator === 0;
  return (
    <div className={s.metricRow}>
      <div className={s.metricName}>
        <span>{name}</span>
        {hint ? <span className={s.metricHint}>{hint}</span> : null}
      </div>
      <div className={`${s.metricValue} num ${!empty && tone === "bad" && rate.numerator > 0 ? s.valueBad : ""}`}>
        {empty ? "n/a" : pct(rate, rate.value !== null && rate.value < 0.1 && rate.value > 0 ? 1 : 0)}
      </div>
      <div className={s.metricPlot}>
        <IntervalBar rate={rate} tone={tone} max={max} width={220} label={name} />
      </div>
      <div className={`${s.metricCounts} num`}>
        {empty ? (
          "No runs apply yet"
        ) : (
          <>
            <span>
              {rate.numerator} of {rate.denominator} runs
            </span>
            <span className={s.metricCi}>95% CI {ciText(rate)}</span>
          </>
        )}
      </div>
    </div>
  );
}
