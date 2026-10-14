import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { DevLogin } from "@/components/dashboard/DevLogin";
import { ButtonLink } from "@/components/ui/Button";
import { GitHubIcon } from "@/components/ui/icons";
import { currentUser, first } from "@/lib/dashboard";
import { safeNext } from "@/lib/format";
import s from "./signin.module.css";

export const metadata: Metadata = { title: "Sign in", robots: { index: false } };

const DEV = process.env.NODE_ENV === "development";

export default function SignInPage(props: PageProps<"/signin">) {
  return (
    <div className={`page ${s.wrap}`}>
      <div className={s.card}>
        <h1 className={s.title}>Sign in to the dashboard</h1>
        <p className={s.lead}>
          Use your GitHub account. Polygraph sees your username and avatar, and which repos you&apos;ve installed its
          GitHub App on. It never asks for your email or reads your code.
        </p>
        <Suspense fallback={<SignInButton next="/app" />}>
          <SignInActions searchParams={props.searchParams} />
        </Suspense>
        <p className={s.small}>
          By signing in you agree to the <a href="/terms">terms</a>. You&apos;ll see the{" "}
          <a href="/privacy">privacy notice</a> before anything is stored.
        </p>
      </div>
      <p className={s.aside}>
        You don&apos;t need an account to use Polygraph. The pytest plugin and its HTML report work entirely offline.
      </p>
    </div>
  );
}

function SignInButton({ next }: { next: string }) {
  return (
    <ButtonLink href={`/api/auth/github/login?next=${encodeURIComponent(next)}`} variant="primary" className={s.cta}>
      <GitHubIcon />
      Continue with GitHub
    </ButtonLink>
  );
}

async function SignInActions({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const next = safeNext(first(sp.next));
  const error = first(sp.error);
  const user = await currentUser();
  if (user) redirect(next);
  return (
    <>
      {error ? (
        <p className={s.error} role="alert">
          {error === "access_denied"
            ? "GitHub sign-in was cancelled. You can try again whenever you like."
            : "Sign-in didn't finish. Start again, and if it keeps failing, check the status page."}
        </p>
      ) : null}
      <SignInButton next={next} />
      {DEV ? <DevLogin next={next} /> : null}
    </>
  );
}
