import { IntervalBar } from "@/components/charts/Interval";
import { ciText, pct } from "@/lib/format";
import type { Leaderboard, Rate } from "@/lib/types";
import s from "./marketing.module.css";

function RateCell({ rate, tone, label }: { rate: Rate; tone: "good" | "bad"; label: string }) {
  return (
    <td className={s.lbRate}>
      <span className={`num ${s.lbValue}`}>{pct(rate)}</span>
      <IntervalBar rate={rate} tone={tone} width={96} label={label} />
      <span className={`num ${s.lbCi}`}>{rate.denominator ? ciText(rate) : ""}</span>
    </td>
  );
}

export function LeaderboardTable({ board }: { board: Leaderboard | null }) {
  if (!board || board.entries.length === 0) {
    return (
      <div className={s.lbEmpty}>
        <p className={s.lbEmptyTitle}>The first benchmark results aren&apos;t published yet.</p>
        <p>
          Predictions are committed before any paid runs, and every number here comes from a real, reproducible
          run against LangGraph, OpenAI Agents SDK, Pydantic AI and smolagents with the same model and the same
          faults. Nothing on this table is estimated or filled in by hand.
        </p>
        {!board ? <p className={s.lbEmptyNote}>The results service can&apos;t be reached right now.</p> : null}
      </div>
    );
  }
  const entries = [...board.entries].sort(
    (a, b) => (a.silent_lie.value ?? 1) - (b.silent_lie.value ?? 1) || a.framework.localeCompare(b.framework),
  );
  return (
    <div className={s.lbWrap}>
      <table className={s.lb}>
        <caption className="visually-hidden">
          Benchmark results per framework, with 95% confidence intervals
        </caption>
        <thead>
          <tr>
            <th scope="col">Framework</th>
            <th scope="col">Setup</th>
            <th scope="col">Silent lie</th>
            <th scope="col">Double action</th>
            <th scope="col">Hijack</th>
            <th scope="col">Recovered</th>
            <th scope="col" className={s.lbNum}>
              Runs
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.report_id}>
              <th scope="row">
                <span className={s.lbFramework}>{e.framework}</span>
                {e.framework_version ? <span className={`${s.lbMeta} mono`}>{e.framework_version}</span> : null}
              </th>
              <td>
                <span>{e.config_label === "best-practice" ? "Maintainer config" : "Defaults"}</span>
                {e.model ? <span className={`${s.lbMeta} mono`}>{e.model}</span> : null}
              </td>
              <RateCell rate={e.silent_lie} tone="bad" label={`${e.framework} silent lie`} />
              <RateCell rate={e.double_action} tone="bad" label={`${e.framework} double action`} />
              <RateCell rate={e.hijack} tone="bad" label={`${e.framework} hijack`} />
              <RateCell rate={e.success} tone="good" label={`${e.framework} recovered`} />
              <td className={`${s.lbNum} num`}>{e.runs}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
