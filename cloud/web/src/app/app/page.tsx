import Link from "next/link";
import { Suspense } from "react";
import { SyncButton } from "@/components/dashboard/SyncButton";
import { ButtonLink } from "@/components/ui/Button";
import { RelTime } from "@/components/ui/RelTime";
import { apiGet } from "@/lib/api-server";
import { projectBase, requireUser } from "@/lib/dashboard";
import { pct } from "@/lib/format";
import type { Paged, Project } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

const APP_SLUG = process.env.GITHUB_APP_SLUG || "polygraph-for-mcp";

export default function ProjectsPage() {
  return (
    <div className="page">
      <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "18rem", marginTop: "2.5rem" }} />}>
        <Projects />
      </Suspense>
    </div>
  );
}

async function Projects() {
  const user = await requireUser("/app");
  const page = await apiGet<Paged<Project>>("/projects?limit=100");
  const installUrl = `https://github.com/apps/${APP_SLUG}/installations/new`;

  return (
    <div className={s.stack}>
      <div className={s.pageHead}>
        <div>
          <h1 className={s.pageTitle}>Projects</h1>
          <p className={s.faint}>
            Repos you can see through the Polygraph GitHub App, signed in as <strong>{user.login}</strong>.
          </p>
        </div>
        <div className={s.pageActions}>
          <SyncButton />
          <ButtonLink href={installUrl} external variant="secondary" small>
            Add repos on GitHub
          </ButtonLink>
        </div>
      </div>

      {page.items.length === 0 ? (
        <div className={s.emptyState}>
          <h2 className={s.h2}>No projects yet</h2>
          <p className={s.lede}>
            Install the Polygraph GitHub App on the repos you want to track. It asks only for read-only metadata, so it
            can tell who may see each repo, and it never reads your code.
          </p>
          <p className={s.faint}>
            Public repos also appear here automatically after their first upload from GitHub Actions.
          </p>
          <div className={s.pageActions}>
            <ButtonLink href={installUrl} external variant="primary">
              Install the GitHub App
            </ButtonLink>
            <ButtonLink href="/docs" variant="quiet">
              Read the quickstart
            </ButtonLink>
          </div>
        </div>
      ) : (
        <ul className={s.projectList}>
          {page.items.map((p) => (
            <li key={p.id}>
              <Link href={projectBase(p.owner, p.repo)} className={s.projectRow}>
                <span className={s.projectName}>
                  <span className={s.faint}>{p.owner} /</span> {p.repo}
                </span>
                <span className={s.projectStat}>
                  {p.latest && p.latest.status === "ready" ? (
                    <>
                      <span className={`num ${p.latest.silent_lie.numerator > 0 ? s.textBad : ""}`}>
                        {pct(p.latest.silent_lie, 1)}
                      </span>{" "}
                      <span className={s.faint}>silent lie</span>
                    </>
                  ) : (
                    <span className={s.faint}>No reports yet</span>
                  )}
                </span>
                <span className={s.projectStat}>
                  {p.latest && p.latest.status === "ready" ? (
                    <>
                      <span className="num">
                        {p.latest.coverage.covered}/{p.latest.coverage.applicable}
                      </span>{" "}
                      <span className={s.faint}>covered</span>
                    </>
                  ) : null}
                </span>
                <span className={`${s.projectStat} ${s.faint}`}>
                  {p.is_private ? "Private" : "Public"}, <RelTime iso={p.last_report_at} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
