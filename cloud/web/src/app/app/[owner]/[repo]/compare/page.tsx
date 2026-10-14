import Link from "next/link";
import { Suspense } from "react";
import { IntervalBar } from "@/components/charts/Interval";
import { CompareForm } from "@/components/dashboard/CompareForm";
import { ComparePairs } from "@/components/dashboard/ComparePairs";
import { ApiError, apiGet } from "@/lib/api-server";
import { apiProjectPath, first, loadProject, projectBase, qs } from "@/lib/dashboard";
import { ciText, pct, shortSha } from "@/lib/format";
import type { Compare, CompareMetric, Paged, Rate, ReportLite } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

export default function ComparePage(props: PageProps<"/app/[owner]/[repo]/compare">) {
  return (
    <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "24rem" }} />}>
      <CompareView params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}

const METRICS: { key: CompareMetric; label: string; lowerIsBetter: boolean }[] = [
  { key: "silent_lie", label: "Silent lie", lowerIsBetter: true },
  { key: "double_action", label: "Double action", lowerIsBetter: true },
  { key: "hijack", label: "Hijack", lowerIsBetter: true },
  { key: "success", label: "Recovered", lowerIsBetter: false },
];

function label(r: ReportLite) {
  const where = r.pr_number ? `PR #${r.pr_number}` : (r.branch ?? "no branch");
  return `${shortSha(r.sha)} on ${where}`;
}

/** Only call a change real when the two 95 % intervals don't overlap. */
function change(base: Rate, head: Rate, lowerIsBetter: boolean) {
  if (base.value === null || head.value === null) return { text: "n/a", tone: "" };
  const d = head.value - base.value;
  if (Math.abs(d) < 0.0005) return { text: "No change", tone: "" };
  const overlap =
    base.ci_low !== null &&
    base.ci_high !== null &&
    head.ci_low !== null &&
    head.ci_high !== null &&
    head.ci_low <= base.ci_high &&
    base.ci_low <= head.ci_high;
  const better = lowerIsBetter ? d < 0 : d > 0;
  const pts = `${d > 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} pts`;
  return {
    text: overlap ? `${pts}, within noise` : `${pts}, ${better ? "better" : "worse"}`,
    tone: overlap ? "" : better ? s.textGood : s.textBad,
  };
}

async function CompareView({
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
  const project = await loadProject(owner, repo, `${base}/compare`);
  const recent = await apiGet<Paged<ReportLite>>(`${api}/reports${qs({ limit: 40 })}`);
  const ready = recent.items.filter((r) => r.status === "ready");

  const headId = first(sp.head) ?? ready[0]?.id ?? "";
  const baseRef = first(sp.base) ?? `branch:${project.default_branch}`;

  const baseOptions = [
    { value: `branch:${project.default_branch}`, label: `Latest on ${project.default_branch}` },
    ...ready.map((r) => ({ value: r.id, label: label(r) })),
  ];
  const headOptions = ready.map((r) => ({ value: r.id, label: label(r) }));

  if (ready.length === 0) {
    return <p className={s.notice}>There are no ready reports to compare yet.</p>;
  }

  let cmp: Compare | null = null;
  let problem: string | null = null;
  try {
    cmp = await apiGet<Compare>(`${api}/compare${qs({ base: baseRef, head: headId })}`);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 409 || e.status === 400)) problem = e.message;
    else throw e;
  }

  const pairs = cmp?.pairs ?? [];

  return (
    <div className={s.stack}>
      <CompareForm baseOptions={baseOptions} headOptions={headOptions} base={baseRef} head={headId} />

      {problem ? <p className={s.notice}>{problem}</p> : null}

      {cmp ? (
        <>
          <section className={s.panel} aria-labelledby="cmp-title">
            <div className={s.panelHead}>
              <h2 id="cmp-title">
                <Link href={`${base}/reports/${cmp.head.id}`}>{label(cmp.head)}</Link>
                <span className={s.faint}> against </span>
                <Link href={`${base}/reports/${cmp.base.id}`}>{label(cmp.base)}</Link>
              </h2>
              <p className={s.faint}>
                A change counts as better or worse only when the two 95% intervals don&apos;t overlap.
              </p>
            </div>
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th scope="col">Metric</th>
                    <th scope="col">Base</th>
                    <th scope="col">Compare</th>
                    <th scope="col">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {METRICS.map((m) => {
                    const v = cmp!.metrics[m.key];
                    const c = change(v.base, v.head, m.lowerIsBetter);
                    const tone = m.lowerIsBetter ? "bad" : "good";
                    return (
                      <tr key={m.key}>
                        <th scope="row">{m.label}</th>
                        <td>
                          <span className={s.cmpCell}>
                            <span className="num">{pct(v.base, 1)}</span>
                            <IntervalBar rate={v.base} tone={tone} width={120} label={`Base ${m.label}`} />
                            <span className={`${s.faint} num`}>{ciText(v.base)}</span>
                          </span>
                        </td>
                        <td>
                          <span className={s.cmpCell}>
                            <span className="num">{pct(v.head, 1)}</span>
                            <IntervalBar rate={v.head} tone={tone} width={120} label={`Compare ${m.label}`} />
                            <span className={`${s.faint} num`}>{ciText(v.head)}</span>
                          </span>
                        </td>
                        <td className={c.tone}>{c.text}</td>
                      </tr>
                    );
                  })}
                  <tr>
                    <th scope="row">Coverage</th>
                    <td className="num">
                      {cmp.coverage.base.covered}/{cmp.coverage.base.applicable}
                    </td>
                    <td className="num">
                      {cmp.coverage.head.covered}/{cmp.coverage.head.applicable}
                    </td>
                    <td
                      className={
                        cmp.coverage.head.covered > cmp.coverage.base.covered
                          ? s.textGood
                          : cmp.coverage.head.covered < cmp.coverage.base.covered
                            ? s.textBad
                            : ""
                      }
                    >
                      {cmp.coverage.head.covered === cmp.coverage.base.covered
                        ? "No change"
                        : `${cmp.coverage.head.covered > cmp.coverage.base.covered ? "+" : "−"}${Math.abs(
                            cmp.coverage.head.covered - cmp.coverage.base.covered,
                          )} pairs`}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className={s.section} aria-labelledby="pairs-title">
            <h2 id="pairs-title" className={s.h2}>
              By tool and fault
            </h2>
            <p className={s.faint}>Largest changes first. Pairs tested on only one side show as not run.</p>
            <ComparePairs pairs={pairs} />
          </section>
        </>
      ) : null}
    </div>
  );
}
