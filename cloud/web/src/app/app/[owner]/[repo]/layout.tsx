import { Suspense } from "react";
import { ProjectTabs } from "@/components/dashboard/ProjectTabs";
import { RelTime } from "@/components/ui/RelTime";
import { loadProject, projectBase } from "@/lib/dashboard";
import s from "@/components/dashboard/dashboard.module.css";

export default function ProjectLayout({ children, params }: LayoutProps<"/app/[owner]/[repo]">) {
  return (
    <div className="page">
      <Suspense fallback={<div className={s.projectHeadSkeleton} />}>
        <ProjectHead params={params} />
      </Suspense>
      {children}
    </div>
  );
}

async function ProjectHead({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const base = projectBase(owner, repo);
  const project = await loadProject(owner, repo, base);
  return (
    <div className={s.projectHead}>
      <div className={s.projectTitleRow}>
        <h1 className={s.projectTitle}>
          <span className={s.projectOwner}>{project.owner} /</span> {project.repo}
        </h1>
        <span className={s.visibility}>
          {project.is_private ? "Private repo" : project.public_results ? "Public results" : "Results hidden"}
        </span>
      </div>
      <p className={s.projectMeta}>
        Last report <RelTime iso={project.last_report_at} />
        <span aria-hidden="true" className={s.metaSep} />
        Default branch <code>{project.default_branch}</code>
        {project.role === "viewer" ? (
          <>
            <span aria-hidden="true" className={s.metaSep} />
            You&apos;re viewing public results
          </>
        ) : null}
      </p>
      <ProjectTabs base={base} isAdmin={project.role === "admin"} />
    </div>
  );
}
