import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ConfirmAction } from "@/components/dashboard/ConfirmAction";
import { ProjectToggles } from "@/components/dashboard/ProjectToggles";
import { TokenManager } from "@/components/dashboard/TokenManager";
import { apiGet } from "@/lib/api-server";
import { apiProjectPath, loadProject, projectBase } from "@/lib/dashboard";
import type { UploadToken } from "@/lib/types";
import s from "@/components/dashboard/dashboard.module.css";

export default function SettingsPage(props: PageProps<"/app/[owner]/[repo]/settings">) {
  return (
    <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "24rem" }} />}>
      <Settings params={props.params} />
    </Suspense>
  );
}

async function Settings({ params }: { params: Promise<{ owner: string; repo: string }> }) {
  const { owner, repo } = await params;
  const base = projectBase(owner, repo);
  const api = apiProjectPath(owner, repo);
  const project = await loadProject(owner, repo, `${base}/settings`);
  if (project.role !== "admin") notFound();
  const tokens = await apiGet<{ items: UploadToken[] }>(`${api}/tokens`);

  return (
    <div className={s.settings}>
      <section className={s.settingsSection} aria-labelledby="vis-title">
        <div>
          <h2 id="vis-title">Who can see results</h2>
          <p className={s.faint}>Visibility follows GitHub. Only repo admins can change these.</p>
        </div>
        <ProjectToggles project={project} apiPath={api} />
      </section>

      <section className={s.settingsSection} aria-labelledby="tokens" id="tokens">
        <div>
          <h2>Upload tokens</h2>
          <p className={s.faint}>
            For <code>polygraph upload</code> from your own machine. Tokens are stored only as a hash and can be revoked
            at any time.
          </p>
        </div>
        <TokenManager tokens={tokens.items} apiPath={api} />
      </section>

      <section className={s.settingsSection} aria-labelledby="danger-title">
        <div>
          <h2 id="danger-title">Delete this project</h2>
          <p className={s.faint}>
            Removes every report, run file and upload token for {project.full_name}. A new upload from CI creates the
            project again.
          </p>
        </div>
        <div>
          <ConfirmAction
            label="Delete project"
            confirmLabel="Delete project and all its data"
            description="This can't be undone."
            path={api}
            redirectTo="/app"
            requireText={project.full_name}
          />
        </div>
      </section>
    </div>
  );
}
