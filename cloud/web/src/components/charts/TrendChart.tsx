"use client";

import Link from "next/link";
import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ciText, dateTime, pct, shortSha } from "@/lib/format";
import type { Rate, TrendPoint } from "@/lib/types";
import s from "./charts.module.css";

type MetricKey = "silent_lie" | "double_action" | "hijack" | "success" | "coverage";

const METRICS: { key: MetricKey; label: string; tone: "bad" | "good" }[] = [
  { key: "silent_lie", label: "Silent lie", tone: "bad" },
  { key: "double_action", label: "Double action", tone: "bad" },
  { key: "hijack", label: "Hijack", tone: "bad" },
  { key: "success", label: "Recovered", tone: "good" },
  { key: "coverage", label: "Coverage", tone: "good" },
];

function rateOf(p: TrendPoint, m: MetricKey): Rate {
  if (m === "coverage") {
    const v = p.coverage.applicable ? p.coverage.covered / p.coverage.applicable : null;
    return { numerator: p.coverage.covered, denominator: p.coverage.applicable, value: v, ci_low: v, ci_high: v };
  }
  return p[m];
}

function niceMax(v: number) {
  if (v <= 0.05) return 0.05;
  if (v <= 0.1) return 0.1;
  if (v <= 0.25) return 0.25;
  if (v <= 0.5) return 0.5;
  return 1;
}

export function TrendChart({ points, reportsBase }: { points: TrendPoint[]; reportsBase: string }) {
  const [metric, setMetric] = useState<MetricKey>("silent_lie");
  const [hover, setHover] = useState<number | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const tabsId = useId();

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const sorted = useMemo(
    () => [...points].sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [points],
  );
  const meta = METRICS.find((m) => m.key === metric)!;
  const rates = sorted.map((p) => rateOf(p, metric));

  const H = 220;
  const pad = { l: 44, r: 16, t: 14, b: 28 };
  const iw = width - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const top =
    metric === "success" || metric === "coverage"
      ? 1
      : niceMax(Math.max(0.05, ...rates.map((r) => r.ci_high ?? r.value ?? 0)));
  const x = (i: number) => pad.l + (sorted.length <= 1 ? iw / 2 : (i / (sorted.length - 1)) * iw);
  const y = (v: number) => pad.t + ih - (Math.min(v, top) / top) * ih;

  const valid = rates.map((r, i) => ({ r, i })).filter(({ r }) => r.value !== null);
  const line = valid.map(({ r, i }, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(r.value!).toFixed(1)}`).join("");
  const band =
    valid.length > 0
      ? valid.map(({ r, i }, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(r.ci_high!).toFixed(1)}`).join("") +
        [...valid]
          .reverse()
          .map(({ r, i }) => `L${x(i).toFixed(1)},${y(r.ci_low!).toFixed(1)}`)
          .join("") +
        "Z"
      : "";
  const ticks = [0, top / 2, top];

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    if (sorted.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    let best = 0;
    let bestD = Infinity;
    sorted.forEach((_, i) => {
      const d = Math.abs(x(i) - px);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    setHover(best);
  }

  const hp = hover !== null ? sorted[hover] : null;
  const hr = hover !== null ? rates[hover] : null;

  return (
    <div className={s.trend}>
      <div className={s.tabs} role="tablist" aria-label="Metric" id={tabsId}>
        {METRICS.map((m) => (
          <button
            key={m.key}
            role="tab"
            type="button"
            aria-selected={metric === m.key}
            className={s.tab}
            onClick={() => setMetric(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div ref={wrap} className={s.trendPlot}>
        {sorted.length === 0 ? (
          <p className={s.emptyNote}>No reports on this branch yet.</p>
        ) : (
          <svg
            width={width}
            height={H}
            viewBox={`0 0 ${width} ${H}`}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
            role="img"
            aria-label={`${meta.label} over the last ${sorted.length} reports. Latest ${pct(rates[rates.length - 1])}.`}
          >
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className={s.gridline} />
                <text x={pad.l - 8} y={y(t)} className={s.axisText} textAnchor="end" dominantBaseline="middle">
                  {Math.round(t * 100)}%
                </text>
              </g>
            ))}
            {band ? <path d={band} className={`${s.band} ${s[`band_${meta.tone}`]}`} /> : null}
            {line ? <path d={line} className={`${s.trendLine} ${s[`line_${meta.tone}`]}`} /> : null}
            {sorted.length === 1 && valid.length === 1 && valid[0].r.ci_low !== null ? (
              // One report: there's no band yet, so show its interval as a whisker.
              <line
                x1={x(0)}
                x2={x(0)}
                y1={y(valid[0].r.ci_low)}
                y2={y(valid[0].r.ci_high!)}
                className={`${s.trendLine} ${s[`line_${meta.tone}`]}`}
              />
            ) : null}
            {valid.map(({ r, i }) => (
              <circle
                key={i}
                cx={x(i)}
                cy={y(r.value!)}
                r={hover === i || sorted.length === 1 ? 4.5 : 2.75}
                className={`${s.trendDot} ${s[`line_${meta.tone}`]}`}
              />
            ))}
            {sorted.length === 1 ? (
              <text x={x(0) + 12} y={pad.t + 10} className={s.axisText}>
                One report so far. The line starts with the next one.
              </text>
            ) : null}
            {sorted.length > 1 ? (
              <>
                <text x={pad.l} y={H - 8} className={s.axisText}>
                  {dateTime(sorted[0].created_at)}
                </text>
                <text x={width - pad.r} y={H - 8} className={s.axisText} textAnchor="end">
                  {dateTime(sorted[sorted.length - 1].created_at)}
                </text>
              </>
            ) : null}
            {hover !== null ? (
              <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + ih} className={s.hoverLine} />
            ) : null}
          </svg>
        )}

        {hp && hr ? (
          <div
            className={s.tooltip}
            style={{ left: Math.min(Math.max(x(hover!) - 90, 0), width - 200) }}
            aria-hidden="true"
          >
            <strong className="num">{pct(hr, 1)}</strong>
            <span>
              {hr.numerator} of {hr.denominator}
              {metric !== "coverage" && hr.ci_low !== null ? `, CI ${ciText(hr)}` : ""}
            </span>
            <span className="mono">{shortSha(hp.sha)}</span>
            <span>{dateTime(hp.created_at)}</span>
          </div>
        ) : null}
      </div>

      <table className="visually-hidden">
        <caption>{meta.label} by report</caption>
        <thead>
          <tr>
            <th>Report</th>
            <th>Date</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => (
            <tr key={p.report_id}>
              <td>
                <Link href={`${reportsBase}/${p.report_id}`}>{shortSha(p.sha)}</Link>
              </td>
              <td>{dateTime(p.created_at)}</td>
              <td>{pct(rates[i], 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
