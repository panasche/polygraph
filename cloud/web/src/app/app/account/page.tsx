import { Suspense } from "react";
import { ConfirmAction } from "@/components/dashboard/ConfirmAction";
import { ButtonLink } from "@/components/ui/Button";
import { RelTime } from "@/components/ui/RelTime";
import { requireUser } from "@/lib/dashboard";
import s from "@/components/dashboard/dashboard.module.css";

export default function AccountPage() {
  return (
    <div className="page">
      <Suspense fallback={<div className={s.skeletonBlock} style={{ height: "18rem", marginTop: "2.5rem" }} />}>
        <Account />
      </Suspense>
    </div>
  );
}

async function Account() {
  const user = await requireUser("/app/account");
  return (
    <div className={s.stack}>
      <div className={s.pageHead}>
        <div>
          <h1 className={s.pageTitle}>Account and data</h1>
          <p className={s.faint}>
            Signed in with GitHub as <strong>{user.login}</strong> since <RelTime iso={user.created_at} />.
          </p>
        </div>
      </div>

      <div className={s.settings}>
        <section className={s.settingsSection} aria-labelledby="what-title">
          <div>
            <h2 id="what-title">What we keep about you</h2>
          </div>
          <dl className={s.keptList}>
            <div>
              <dt>GitHub user id and username</dt>
              <dd>To know who you are and which repos you can see.</dd>
            </div>
            <div>
              <dt>Avatar link</dt>
              <dd>Shown in the top bar. The image stays on GitHub.</dd>
            </div>
            <div>
              <dt>Privacy notice you accepted</dt>
              <dd>
                Version <code>{user.consented_privacy_version ?? "none"}</code>.
              </dd>
            </div>
          </dl>
          <p className={s.faint}>No email address, no code, no API keys. Reports belong to projects, not to you.</p>
        </section>

        <section className={s.settingsSection} aria-labelledby="export-title">
          <div>
            <h2 id="export-title">Export your data</h2>
            <p className={s.faint}>A JSON file with your account and every project you administer.</p>
          </div>
          <div>
            <ButtonLink href="/api/me/export" variant="secondary" download>
              Download export
            </ButtonLink>
          </div>
        </section>

        <section className={s.settingsSection} aria-labelledby="delete-title">
          <div>
            <h2 id="delete-title">Delete your account</h2>
            <p className={s.faint}>
              Signs you out everywhere and removes your account. Projects where you&apos;re the only admin are deleted
              too, with all their reports. To remove the GitHub App, uninstall it in your GitHub settings.
            </p>
          </div>
          <div>
            <ConfirmAction
              label="Delete account"
              confirmLabel="Delete my account"
              description="This can't be undone."
              path="/me"
              redirectTo="/"
              requireText={user.login}
              hardNavigate
            />
          </div>
        </section>
      </div>
    </div>
  );
}
