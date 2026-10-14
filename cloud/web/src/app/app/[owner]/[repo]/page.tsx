import Link from "next/link";
import { Suspense } from "react";
import { MetricRow } from "@/components/charts/Interval";
import { TrendChart } from "@/components/charts/TrendChart";
import { BranchSelect } from "@/components/dashboard/BranchSelect";
import { CoverageSummary } from "@/components/dashboard/CoverageSummary";
import { FirstUpload } from "@/components/dashboard/FirstUpload";
import { ReportsTable } from "@/components/dashboard/ReportsTable";
import { CopyCommand } from "@/components/ui/CopyCommand";
import { RelTime } from "@/components/ui/RelTime";
import { apiGet } from "@/lib/api-server";
import { apiProjectPath, first, loadProject, projectBase, qs } from "@/lib/dashboard";
import { shortSha } from "@/lib/format";
import type { Branch, Paged, ReportDetail, ReportLite, Trends } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

export default function OverviewPage(props: PageProps<"/app/[owner]/[repo]">) {
  return (
    <Suspense fallback={<OverviewSkeleton />}>
      <Overview params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

function OverviewSkeleton() {
  return (
    <div className={s.stack} aria-busy="true">
      <div className={s.skeletonBlock} style={{ height: "14rem" }} />
      <div className={s.skeletonBlock} style={{ height: "16rem" }} />
    </div>
  );
}

async function Overview({
  params,
  searchParams,
}: {
  params: Promise<{ owner: string; repo: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { owner, repo } = await params;
  const sp = await searchParams;
  const base = projectBase(owner, repo);
  const api = apiProjectPath(owner, repo);
  const project = await loadProject(owner, repo, base);
  const branch = first(sp.branch) ?? project.default_branch;

  const [branches, latestPage, trends] = await Promise.all([
    apiGet<{ items: Branch[] }>(`${api}/branches`),
    apiGet<Paged<ReportLite>>(`${api}/reports${qs({ branch, limit: 10 })}`),
    apiGet<Trends>(`${api}/trends${qs({ branch, days: 90 })}`),
  ]);

  if (branches.items.length === 0) {
    return <FirstUpload project={project} />;
  }

  const ready = latestPage.items.find((r) => r.status === "ready") ?? null;
  const latest = ready ? await apiGet<ReportDetail>(`${api}/reports/${ready.id}`) : null;
  const origin = process.env.PUBLIC_BASE_URL || "https://polygraphmcp.dev";
  const badgeUrl = `${origin}/api/badge/${project.owner}/${project.repo}.svg`;

  return (
    <div className={s.stack}>
      <div className={s.toolbar}>
        <BranchSelect branches={branches.items} value={branch} />
        {latest ? (
          <p className={s.faint}>
            Latest ready report <Link href={`${base}/reports/${latest.id}`}>{shortSha(latest.sha)}</Link>,{" "}
            <RelTime iso={latest.created_at} />
          </p>
        ) : null}
      </div>

      {latest ? (
        <div className={s.overviewGrid}>
          <section className={s.panel} aria-labelledby="honesty-title">
            <div className={s.panelHead}>
              <h2 id="honesty-title">Can you trust what it says?</h2>
              <p className={s.faint}>
                {latest.framework ?? "Agent"}
                {latest.model ? <span className="mono"> {latest.model}</span> : null}, {latest.faulted_runs} faulted
                runs
              </p>
            </div>
            <MetricRow name="Silent lie" hint="Said done, wasn't" rate={latest.silent_lie} tone="bad" />
            <MetricRow name="Double action" hint="One-time action done twice" rate={latest.double_action} tone="bad" />
            <MetricRow name="Hijack" hint="Followed injected orders" rate={latest.hijack} tone="bad" />
            <MetricRow name="Recovered" hint="Finished correctly anyway" rate={latest.success} tone="good" />
          </section>

          <section className={s.panel} aria-labelledby="coverage-title">
            <div className={s.panelHead}>
              <h2 id="coverage-title">How much did you test?</h2>
            </div>
            <CoverageSummary coverage={latest.coverage_detail} />
          </section>
        </div>
      ) : (
        <p className={s.notice}>
          No ready report on <code>{branch}</code> yet. Reports appear here once the worker has processed them.
        </p>
      )}

      <section className={s.panel} aria-labelledby="trend-title">
        <div className={s.panelHead}>
          <h2 id="trend-title">Over time on {branch}</h2>
          <p className={s.faint}>Last 90 days. Shaded bands are 95% confidence intervals.</p>
        </div>
        <TrendChart points={trends.points} reportsBase={`${base}/reports`} />
      </section>

      <section aria-labelledby="recent-title" className={s.section}>
        <div className={s.sectionHead}>
          <h2 id="recent-title">Recent reports</h2>
          <Link href={`${base}/reports${qs({ branch })}`}>All reports on {branch}</Link>
        </div>
        <ReportsTable reports={latestPage.items} base={base} />
      </section>

      {project.public_results && !project.is_private ? (
        <section aria-labelledby="badge-title" className={s.section}>
          <h2 id="badge-title" className={s.h2}>
            README badge
          </h2>
          <p className={s.faint}>Shows the latest silent-lie rate and coverage on {project.default_branch}.</p>
          <div className={s.badgeRow}>
            {/* Served by our own API; plain img keeps it identical to what GitHub renders. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/api/badge/${project.owner}/${project.repo}.svg`} alt="Polygraph badge" height={20} />
            <CopyCommand prompt={false} command={`[![Polygraph](${badgeUrl})](${origin}${base})`} />
          </div>
        </section>
      ) : null}
    </div>
  );
}
