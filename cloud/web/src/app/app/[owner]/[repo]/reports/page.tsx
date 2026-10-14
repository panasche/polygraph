import Link from "next/link";
import { Suspense } from "react";
import { BranchSelect } from "@/components/dashboard/BranchSelect";
import { ReportsTable } from "@/components/dashboard/ReportsTable";
import { apiGet } from "@/lib/api-server";
import { apiProjectPath, first, loadProject, projectBase, qs } from "@/lib/dashboard";
import type { Branch, Paged, ReportLite } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

export default function ReportsPage(props: PageProps<"/app/[owner]/[repo]/reports">) {
  return (
    <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "20rem" }} />}>
      <Reports params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

async function Reports({
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
  const project = await loadProject(owner, repo, `${base}/reports`);
  const branch = first(sp.branch) ?? "";
  const cursor = first(sp.cursor);

  const [branches, page] = await Promise.all([
    apiGet<{ items: Branch[] }>(`${api}/branches`),
    apiGet<Paged<ReportLite>>(`${api}/reports${qs({ branch, cursor, limit: 25 })}`),
  ]);

  return (
    <div className={s.stack}>
      <div className={s.toolbar}>
        <BranchSelect
          branches={[{ name: "", last_report_at: null, reports: 0 }, ...branches.items]}
          value={branch}
        />
        {branch ? <Link href={`${base}/reports`}>Show every branch</Link> : null}
      </div>
      {page.items.length === 0 ? (
        <p className={s.notice}>
          No reports {branch ? (
            <>
              on <code>{branch}</code>
            </>
          ) : (
            `for ${project.full_name}`
          )}{" "}
          yet.
        </p>
      ) : (
        <ReportsTable reports={page.items} base={base} />
      )}
      <div className={s.pager}>
        {cursor ? <Link href={`${base}/reports${qs({ branch })}`}>Newest reports</Link> : <span />}
        {page.next_cursor ? (
          <Link href={`${base}/reports${qs({ branch, cursor: page.next_cursor })}`}>Older reports</Link>
        ) : null}
      </div>
    </div>
  );
}
