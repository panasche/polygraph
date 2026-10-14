import { redirect } from "next/navigation";
import { Suspense } from "react";
import { ConsentForm } from "@/components/dashboard/ConsentForm";
import { currentUser, first, PRIVACY_VERSION, signinHref } from "@/lib/dashboard";
import { safeNext } from "@/lib/format";
import { HOSTING } from "@/lib/hosting";
import s from "@/components/dashboard/dashboard.module.css";

export default function ConsentPage(props: PageProps<"/app/consent">) {
  return (
    <div className={`page ${s.narrow}`}>
      <h1 className={s.pageTitle}>Before you continue</h1>
      <p className={s.lede}>
        Polygraph is run under India&apos;s Digital Personal Data Protection Act, 2023. Here&apos;s exactly what signing
        in means.
      </p>
      <ul className={s.consentPoints}>
        <li>We keep your GitHub user id, username and avatar link. Nothing else about you, and never your email.</li>
        <li>The GitHub App reads only repo metadata, to check who may see each project. It never reads code.</li>
        <li>Test results you upload carry numbers and hashes by default, not the text of tool calls.</li>
        <li>Data is stored in {HOSTING.short}, encrypted in transit and at rest.</li>
        <li>You can export or delete everything at any time from your account page.</li>
      </ul>
      <Suspense fallback={null}>
        <Consent searchParams={props.searchParams} />
      </Suspense>
    </div>
  );
}

async function Consent({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const next = safeNext(first(sp.next));
  const user = await currentUser();
  if (!user) redirect(signinHref(next));
  if (user.consented_privacy_version === PRIVACY_VERSION) redirect(next);
  return <ConsentForm version={PRIVACY_VERSION} next={next} />;
}
