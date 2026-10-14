import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { UserMenu } from "@/components/dashboard/UserMenu";
import { Logo } from "@/components/ui/Logo";
import { currentUser } from "@/lib/dashboard";
import { STATUS_URL } from "@/lib/site";
import s from "@/components/dashboard/dashboard.module.css";

export const metadata: Metadata = {
  title: "Dashboard",
  robots: { index: false, follow: false },
};

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={s.shell}>
      <header className={s.topbar}>
        <div className={`page ${s.topbarInner}`}>
          <Logo href="/app" sub={false} />
          <nav aria-label="Dashboard" className={s.topnav}>
            <Link href="/app">Projects</Link>
            <Link href="/benchmark">Benchmark</Link>
            <Link href="/docs">Docs</Link>
          </nav>
          <Suspense fallback={<span className={s.userPlaceholder} />}>
            <TopbarUser />
          </Suspense>
        </div>
      </header>
      <main id="main" className={s.main}>
        {children}
      </main>
      <footer className={s.footer}>
        <div className={`page ${s.footerInner}`}>
          <span>Free service, no uptime promise.</span>
          {STATUS_URL ? <a href={STATUS_URL}>Status</a> : null}
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href="/security">Security</Link>
        </div>
      </footer>
    </div>
  );
}

async function TopbarUser() {
  const user = await currentUser();
  if (!user) {
    return (
      <Link href="/signin" className={s.signinLink}>
        Sign in
      </Link>
    );
  }
  return <UserMenu login={user.login} avatarUrl={user.avatar_url} />;
}
