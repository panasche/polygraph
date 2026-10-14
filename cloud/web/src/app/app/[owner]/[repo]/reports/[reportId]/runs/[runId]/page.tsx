import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { LedgerView } from "@/components/ledger/LedgerView";
import { PayloadView } from "@/components/ledger/PayloadView";
import { CopyCommand } from "@/components/ui/CopyCommand";
import { OutcomeChip } from "@/components/ui/OutcomeChip";
import { apiGetOrNull } from "@/lib/api-server";
import { apiProjectPath, loadProject, projectBase } from "@/lib/dashboard";
import { BAD, OUTCOME_MEANING } from "@/lib/format";
import type { RunDetail } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";
import l from "./run.module.css";

export default function RunPage(props: PageProps<"/app/[owner]/[repo]/reports/[reportId]/runs/[runId]">) {
  return (
    <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "30rem" }} />}>
      <Run params={props.params} />
    </Suspense>
  );
}

async function Run({ params }: { params: Promise<{ owner: string; repo: string; reportId: string; runId: string }> }) {
  const { owner, repo, reportId, runId } = await params;
  const base = projectBase(owner, repo);
  await loadProject(owner, repo, `${base}/reports/${reportId}/runs/${runId}`);
  const run = await apiGetOrNull<RunDetail>(
    `${apiProjectPath(owner, repo)}/reports/${encodeURIComponent(reportId)}/runs/${encodeURIComponent(runId)}`,
  );
  if (!run) notFound();

  const bad = BAD.has(run.outcome);
  const claimed = run.judge?.claimed_success;

  return (
    <div className={s.stack}>
      <div>
        <p className={s.crumbs}>
          <Link href={`${base}/reports/${reportId}`}>Report</Link>
        </p>
        <h2 className={l.title}>{run.task ?? run.test_id.split("::").pop()}</h2>
        <p className={`${l.testId} mono`}>{run.test_id}</p>
      </div>

      <div className={l.summary}>
        <dl className={l.facts}>
          <div>
            <dt>Fault</dt>
            <dd>
              {run.fault ? (
                <>
                  <code>{run.fault.name}</code> on <code>{run.fault.tool}</code>, call {run.fault.on_call}
                </>
              ) : (
                "None. This is the control run."
              )}
            </dd>
          </div>
          <div>
            <dt>Seed</dt>
            <dd className="mono">{run.seed}</dd>
          </div>
          <div>
            <dt>Repeat</dt>
            <dd className="num">{run.repeat + 1}</dd>
          </div>
          <div>
            <dt>Duration</dt>
            <dd className="num">{(run.duration_ms / 1000).toFixed(2)} s</dd>
          </div>
          {run.tokens ? (
            <div>
              <dt>Tokens</dt>
              <dd className="num">
                {run.tokens.input.toLocaleString("en-IN")} in, {run.tokens.output.toLocaleString("en-IN")} out
              </dd>
            </div>
          ) : null}
          {run.protocol_version ? (
            <div>
              <dt>MCP version</dt>
              <dd className="mono">{run.protocol_version}</dd>
            </div>
          ) : null}
        </dl>
        <div className={l.replay}>
          <p className={s.faint}>Replay with the same fault and recorded tool replies</p>
          <CopyCommand command={`polygraph replay ${run.id} --times 10`} />
        </div>
      </div>

      {!run.content_included ? (
        <p className={s.notice}>
          Payload text wasn&apos;t uploaded for this report, so only fingerprints are shown. The full text is in the
          local HTML report on the machine that ran the tests.
        </p>
      ) : null}

      <section aria-labelledby="ledger-title">
        <h2 id="ledger-title" className="visually-hidden">
          Ledger
        </h2>
        <LedgerView entries={run.ledger} />
      </section>

      <section className={`${l.verdict} ${bad ? l.verdictBad : ""}`} aria-labelledby="verdict-title">
        <div className={l.answer}>
          <h3>The agent&apos;s final answer</h3>
          {run.final_answer ? (
            <PayloadView payload={run.final_answer} long={20} />
          ) : (
            <p className={s.faint}>{run.error ? "None, the agent crashed." : "No answer was recorded."}</p>
          )}
          {run.error ? <pre className={l.error}>{run.error}</pre> : null}
        </div>
        <div className={l.judgement}>
          <h2 id="verdict-title" className={l.verdictTitle}>
            <span>Verdict</span>
            <OutcomeChip outcome={run.outcome} />
          </h2>
          <p className={s.faint}>{OUTCOME_MEANING[run.outcome]}</p>
          {run.reasons.length ? (
            <ul className={l.reasons}>
              {run.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : null}

          <dl className={l.evidence}>
            <div>
              <dt>Did it claim success?</dt>
              <dd>
                {claimed === true ? "Yes" : claimed === false ? "No" : "Couldn't tell"}
                {run.judge ? (
                  <span className={s.faint}>
                    {" "}
                    ({run.judge.method === "llm" ? `model judge${run.judge.model ? `, ${run.judge.model}` : ""}` : run.judge.method === "rules" ? "rule-based judge" : "checked by hand"})
                  </span>
                ) : null}
                {run.judge?.rationale ? <span className={l.rationale}>{run.judge.rationale}</span> : null}
              </dd>
            </div>
            <div>
              <dt>Real state</dt>
              <dd>
                {run.state_checks.length === 0 ? (
                  <span className={s.faint}>No state checks recorded</span>
                ) : (
                  <ul className={l.checks}>
                    {run.state_checks.map((c, i) => (
                      <li key={i} className={c.passed ? l.pass : l.fail}>
                        <span className={l.checkMark}>{c.passed ? "Pass" : "Fail"}</span>
                        <span>
                          <code>{c.name}</code>
                          {c.detail ? <span className={s.faint}> {c.detail}</span> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
            </div>
            {run.second_opinion ? (
              <div>
                <dt>Transcript-only check</dt>
                <dd>
                  {run.second_opinion.tool} {run.second_opinion.flagged_lie ? "flagged a lie" : "saw no lie"}, which{" "}
                  {run.second_opinion.agrees_with_polygraph ? "agrees" : "disagrees"} with the server&apos;s state.
                  {run.second_opinion.detail ? <span className={s.faint}> {run.second_opinion.detail}</span> : null}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>
      </section>
    </div>
  );
}
