import Link from "next/link";
import { RelTime } from "@/components/ui/RelTime";
import { pct, shortSha } from "@/lib/format";
import type { Rate, ReportLite } from "@/lib/types";
import s from "./dashboard.module.css";

function RateTd({ rate, bad }: { rate: Rate; bad?: boolean }) {
  const isBad = bad && rate.numerator > 0;
  return (
    <td className={`num ${s.rateTd} ${isBad ? s.textBad : ""}`}>
      {rate.denominator === 0 ? <span className={s.faint}>n/a</span> : pct(rate, 1)}
    </td>
  );
}

export function StatusPill({ status, reason }: { status: ReportLite["status"]; reason?: string | null }) {
  const label = { queued: "Queued", processing: "Processing", ready: "Ready", rejected: "Rejected" }[status];
  return (
    <span className={`${s.status} ${s[`status_${status}`]}`} title={reason ?? undefined}>
      {label}
    </span>
  );
}

export function ReportsTable({ reports, base }: { reports: ReportLite[]; base: string }) {
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <thead>
          <tr>
            <th scope="col">Report</th>
            <th scope="col">Agent</th>
            <th scope="col" className={s.right}>
              Silent lie
            </th>
            <th scope="col" className={s.right}>
              Double action
            </th>
            <th scope="col" className={s.right}>
              Hijack
            </th>
            <th scope="col" className={s.right}>
              Coverage
            </th>
            <th scope="col" className={s.right}>
              Runs
            </th>
          </tr>
        </thead>
        <tbody>
          {reports.map((r) => (
            <tr key={r.id}>
              <td>
                <Link href={`${base}/reports/${r.id}`} className={s.reportLink}>
                  <code>{shortSha(r.sha)}</code>
                  <span>{r.pr_number ? `PR #${r.pr_number}` : (r.branch ?? "no branch")}</span>
                </Link>
                <span className={s.subline}>
                  <RelTime iso={r.created_at} />
                  {r.status !== "ready" ? <StatusPill status={r.status} reason={r.rejection_reason} /> : null}
                </span>
              </td>
              <td>
                <span>{r.framework ?? "Unknown framework"}</span>
                <span className={`${s.subline} mono`}>{r.model ?? ""}</span>
              </td>
              {r.status === "ready" ? (
                <>
                  <RateTd rate={r.silent_lie} bad />
                  <RateTd rate={r.double_action} bad />
                  <RateTd rate={r.hijack} bad />
                  <td className={`num ${s.right}`}>
                    {r.coverage.covered}/{r.coverage.applicable}
                  </td>
                  <td className={`num ${s.right}`}>{r.runs}</td>
                </>
              ) : (
                <td colSpan={5} className={s.pendingCell}>
                  {r.status === "rejected" ? (r.rejection_reason ?? "This upload was rejected.") : "Waiting for the worker."}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
