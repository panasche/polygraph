import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Heatmap } from "@/components/charts/Heatmap";
import { MetricRow } from "@/components/charts/Interval";
import { OutcomeBar } from "@/components/charts/OutcomeBar";
import { AutoRefresh } from "@/components/dashboard/AutoRefresh";
import { ConfirmAction } from "@/components/dashboard/ConfirmAction";
import { CoverageSummary } from "@/components/dashboard/CoverageSummary";
import { StatusPill } from "@/components/dashboard/ReportsTable";
import { RunFilters } from "@/components/dashboard/RunFilters";
import { OutcomeChip } from "@/components/ui/OutcomeChip";
import { RelTime } from "@/components/ui/RelTime";
import { apiGetOrNull } from "@/lib/api-server";
import { apiProjectPath, first, loadProject, projectBase, qs } from "@/lib/dashboard";
import { BAD, shortSha } from "@/lib/format";
import type { HintValue, ReportDetail } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

export default function ReportPage(props: PageProps<"/app/[owner]/[repo]/reports/[reportId]">) {
  return (
    <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "28rem" }} />}>
      <Report params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

const SOURCE_LABEL: Record<string, string> = { oidc: "GitHub Actions", token: "Upload token" };

function Hint({ hint, label }: { hint: HintValue; label: string }) {
  return (
    <td>
      <span className={hint.value ? s.hintYes : s.hintNo}>{hint.value ? "Yes" : "No"}</span>
      <span className={`${s.hintSource} ${hint.source === "guessed" || hint.source === "default" ? s.hintWeak : ""}`}>
        {hint.source}
      </span>
      <span className="visually-hidden">
        {label} from {hint.source}
      </span>
    </td>
  );
}

async function Report({
  params,
  searchParams,
}: {
  params: Promise<{ owner: string; repo: string; reportId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { owner, repo, reportId } = await params;
  const sp = await searchParams;
  const base = projectBase(owner, repo);
  const project = await loadProject(owner, repo, `${base}/reports/${reportId}`);
  const report = await apiGetOrNull<ReportDetail>(
    `${apiProjectPath(owner, repo)}/reports/${encodeURIComponent(reportId)}`,
  );
  if (!report) notFound();

  const env = report.environment;
  const pending = report.status === "queued" || report.status === "processing";

  const fOutcome = first(sp.outcome);
  const fTool = first(sp.tool);
  const fFault = first(sp.fault);
  const runs = (report.run_list ?? []).filter(
    (r) =>
      (!fOutcome || r.outcome === fOutcome) &&
      (!fTool || r.fault?.tool === fTool) &&
      (!fFault || (fFault === "none" ? r.fault === null : r.fault?.name === fFault)),
  );
  const tools = [...new Set((report.tools ?? []).map((t) => t.name))].sort();
  const faults = [...new Set((report.heatmap ?? []).map((c) => c.fault))].sort();
  const compareHref = `${base}/compare${qs({ head: report.id, base: `branch:${project.default_branch}` })}`;

  return (
    <div className={s.stack}>
      {pending ? <AutoRefresh /> : null}

      <div className={s.reportHead}>
        <div>
          <p className={s.crumbs}>
            <Link href={`${base}/reports`}>Reports</Link>
          </p>
          <h2 className={s.reportTitle}>
            Report <code>{shortSha(report.sha)}</code>
            <StatusPill status={report.status} reason={report.rejection_reason} />
          </h2>
          <dl className={s.facts}>
            <div>
              <dt>Branch</dt>
              <dd>
                {report.pr_number ? `PR #${report.pr_number} into ` : ""}
                <code>{report.branch ?? "unknown"}</code>
              </dd>
            </div>
            <div>
              <dt>Uploaded</dt>
              <dd>
                <RelTime iso={report.created_at} /> from {SOURCE_LABEL[report.source] ?? report.source}
              </dd>
            </div>
            <div>
              <dt>Agent</dt>
              <dd>
                {env?.framework ?? "Unknown framework"}
                {env?.framework_version ? <span className="mono"> {env.framework_version}</span> : null}
                {env?.config_label ? `, ${env.config_label}` : ""}
              </dd>
            </div>
            {env?.model ? (
              <div>
                <dt>Model</dt>
                <dd className="mono">{env.model}</dd>
              </div>
            ) : null}
            {env ? (
              <div>
                <dt>Polygraph</dt>
                <dd>
                  <span className="mono">{env.polygraph_version}</span>, seed <span className="mono">{env.seed}</span>
                  {env.protocol_versions?.length ? (
                    <>
                      , MCP <span className="mono">{env.protocol_versions.join(", ")}</span>
                    </>
                  ) : null}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>
        <div className={s.reportActions}>
          {report.status === "ready" ? (
            <Link href={compareHref} className={s.actionLink}>
              Compare with {project.default_branch}
            </Link>
          ) : null}
          {project.role === "admin" ? (
            <ConfirmAction
              label="Delete report"
              confirmLabel="Delete report"
              description="This removes the report, its numbers and every stored run file. It can't be undone."
              path={`${apiProjectPath(owner, repo)}/reports/${report.id}`}
              redirectTo={`${base}/reports`}
            />
          ) : null}
        </div>
      </div>

      {pending ? (
        <p className={s.notice} role="status">
          This report is still being processed. The page refreshes on its own.
        </p>
      ) : null}
      {report.status === "rejected" ? (
        <p className={`${s.notice} ${s.noticeBad}`} role="alert">
          {report.rejection_reason ?? "This upload was rejected."} Fix the problem and upload again; nothing from this
          upload was kept except this note.
        </p>
      ) : null}

      {report.status === "ready" && report.summary ? (
        <>
          <section className={s.panel} aria-labelledby="outcomes-title">
            <div className={s.panelHead}>
              <h2 id="outcomes-title">How {report.runs} runs ended</h2>
              <p className={s.faint}>
                {report.faulted_runs} under a fault, {report.runs - report.faulted_runs} control runs with no fault.
              </p>
            </div>
            <OutcomeBar outcomes={report.summary.outcomes} />
          </section>

          <div className={s.overviewGrid}>
            <section className={s.panel} aria-labelledby="rates-title">
              <div className={s.panelHead}>
                <h2 id="rates-title">Rates</h2>
              </div>
              <MetricRow name="Silent lie" hint="Said done, wasn't" rate={report.summary.silent_lie} tone="bad" />
              <MetricRow name="Double action" hint="One-time action done twice" rate={report.summary.double_action} tone="bad" />
              <MetricRow name="Hijack" hint="Followed injected orders" rate={report.summary.hijack} tone="bad" />
              <MetricRow name="Loop" hint="Same call 3+ times" rate={report.summary.loop} tone="bad" />
              <MetricRow name="Recovered" hint="Finished correctly anyway" rate={report.summary.success} tone="good" />
              <dl className={s.extraStats}>
                {report.summary.pass_k ? (
                  <div>
                    <dt>
                      pass<sup>{report.summary.pass_k.k}</sup>
                    </dt>
                    <dd className="num">
                      {report.summary.pass_k.passed} of {report.summary.pass_k.groups} tests passed every repeat
                    </dd>
                  </div>
                ) : null}
                {report.summary.cost_multiplier !== null ? (
                  <div>
                    <dt>Cost under faults</dt>
                    <dd className="num">{report.summary.cost_multiplier.toFixed(2)}× the tokens of a clean run</dd>
                  </div>
                ) : null}
              </dl>
            </section>
            <section className={s.panel} aria-labelledby="cov-title">
              <div className={s.panelHead}>
                <h2 id="cov-title">Coverage</h2>
              </div>
              <CoverageSummary coverage={report.coverage_detail} limit={8} />
            </section>
          </div>

          <section className={s.panel} aria-labelledby="heat-title">
            <div className={s.panelHead}>
              <h2 id="heat-title">Where it went wrong</h2>
              <p className={s.faint}>Bad outcomes out of runs, for each tool and fault. Select a cell to see its runs.</p>
            </div>
            <Heatmap
              cells={report.heatmap}
              pairs={report.coverage_detail.pairs}
              tools={report.tools}
              runsHref={`${base}/reports/${report.id}`}
            />
          </section>

          {report.server_claims.length > 0 ? (
            <section className={s.panel} aria-labelledby="claims-title">
              <div className={s.panelHead}>
                <h2 id="claims-title">Did the tools tell the truth?</h2>
                <p className={s.faint}>Polygraph also checks the labels servers put on their own tools.</p>
              </div>
              <ul className={s.claims}>
                {report.server_claims.map((c, i) => (
                  <li key={i} className={c.held ? s.claimHeld : s.claimBroken}>
                    <span className={s.claimVerdict}>{c.held ? "Held" : "Broken"}</span>
                    <div>
                      <p>
                        <code>
                          {c.server}/{c.tool}
                        </code>{" "}
                        says it is {c.claim === "idempotent" ? "safe to repeat" : "read-only"}.
                      </p>
                      <p className={s.faint}>{c.evidence}</p>
                    </div>
                    {c.run_id ? (
                      <Link href={`${base}/reports/${report.id}/runs/${c.run_id}`} className={s.actionLink}>
                        See the run
                      </Link>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className={s.panel} aria-labelledby="tools-title">
            <div className={s.panelHead}>
              <h2 id="tools-title">Tools and their labels</h2>
              <p className={s.faint}>
                Where each label came from. Guessed and default labels are worth confirming in{" "}
                <code>[tool.polygraph.overrides]</code>.
              </p>
            </div>
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th scope="col">Tool</th>
                    <th scope="col">Read-only</th>
                    <th scope="col">Destructive</th>
                    <th scope="col">Safe to repeat</th>
                    <th scope="col">Outside world</th>
                    <th scope="col">Output schema</th>
                  </tr>
                </thead>
                <tbody>
                  {report.tools.map((t) => (
                    <tr key={`${t.server}/${t.name}`}>
                      <th scope="row">
                        <span className={s.faint}>{t.server}/</span>
                        <code>{t.name}</code>
                      </th>
                      <Hint hint={t.hints.read_only} label="read-only" />
                      <Hint hint={t.hints.destructive} label="destructive" />
                      <Hint hint={t.hints.idempotent} label="safe to repeat" />
                      <Hint hint={t.hints.open_world} label="outside world" />
                      <td>{t.has_output_schema ? "Yes" : <span className={s.faint}>No</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className={s.section} aria-labelledby="runs-title" id="runs">
            <div className={s.sectionHead}>
              <h2 id="runs-title">
                Runs <span className={`${s.faint} num`}>{runs.length}</span>
              </h2>
              <RunFilters tools={tools} faults={faults} />
            </div>
            {runs.length === 0 ? (
              <p className={s.notice}>No runs match these filters.</p>
            ) : (
              <div className={s.tableWrap}>
                <table className={s.table}>
                  <thead>
                    <tr>
                      <th scope="col">Test</th>
                      <th scope="col">Fault</th>
                      <th scope="col">Outcome</th>
                      <th scope="col">Why</th>
                      <th scope="col" className={s.right}>
                        Time
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id} className={BAD.has(r.outcome) ? s.rowBad : ""}>
                        <td>
                          <Link href={`${base}/reports/${report.id}/runs/${r.id}`} className={s.runLink}>
                            {r.task ?? r.test_id.split("::").pop()}
                          </Link>
                          <span className={`${s.subline} mono`}>
                            {r.test_id}
                            {r.repeat ? `, repeat ${r.repeat + 1}` : ""}
                          </span>
                        </td>
                        <td>
                          {r.fault ? (
                            <code>
                              {r.fault.tool} × {r.fault.name}
                            </code>
                          ) : (
                            <span className={s.faint}>Control</span>
                          )}
                        </td>
                        <td>
                          <OutcomeChip outcome={r.outcome} />
                        </td>
                        <td className={s.why}>{r.reasons[0] ?? ""}</td>
                        <td className={`num ${s.right}`}>{(r.duration_ms / 1000).toFixed(1)} s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {report.run_list.length >= 2000 ? (
              <p className={s.faint}>
                Showing the first 2,000 runs, bad outcomes first. The full set is in the results file.
              </p>
            ) : null}
          </section>
        </>
      ) : null}
    </div>
  );
}
