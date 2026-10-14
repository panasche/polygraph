import Link from "next/link";
import { ButtonLink } from "@/components/ui/Button";
import { GitHubIcon } from "@/components/ui/icons";
import { Logo } from "@/components/ui/Logo";
import { HOSTING } from "@/lib/hosting";
import { SOURCE_URL, STATUS_URL } from "@/lib/site";
import s from "./marketing.module.css";

export const GITHUB_URL = SOURCE_URL;

export function SiteHeader() {
  return (
    <header className={s.header}>
      <div className={`page ${s.headerInner}`}>
        <Logo />
        <nav aria-label="Main" className={s.nav}>
          <Link href="/benchmark">Benchmark</Link>
          <Link href="/recipes">Fix recipes</Link>
          <Link href="/docs">Docs</Link>
          <a href={GITHUB_URL} className={s.navGithub}>
            <GitHubIcon />
            <span>GitHub</span>
          </a>
        </nav>
        <ButtonLink href="/signin" variant="secondary" small className={s.headerCta}>
          Open dashboard
        </ButtonLink>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className={s.footer}>
      <div className={`page ${s.footerInner}`}>
        <div className={s.footerLead}>
          <Logo />
          <p>Free and open source under the Apache 2.0 licence. Hosted results are stored in {HOSTING.short}.</p>
          <p className={s.footerNote}>
            Not affiliated with Nx Polygraph or agent-polygraph. See{" "}
            <Link href="/docs#how-is-this-different">how it differs</Link>.
          </p>
        </div>
        <nav aria-label="Product" className={s.footerCol}>
          <h2>Product</h2>
          <Link href="/docs">Get started</Link>
          <Link href="/benchmark">Benchmark</Link>
          <Link href="/recipes">Fix recipes</Link>
          <Link href="/signin">Dashboard</Link>
        </nav>
        <nav aria-label="Project" className={s.footerCol}>
          <h2>Project</h2>
          <a href={GITHUB_URL}>Source code</a>
          <a href={`${GITHUB_URL}/issues`}>Issues</a>
          <a href="https://pypi.org/project/pytest-polygraph/">PyPI</a>
          {STATUS_URL ? <a href={STATUS_URL}>Status</a> : null}
        </nav>
        <nav aria-label="Legal and trust" className={s.footerCol}>
          <h2>Trust</h2>
          <Link href="/privacy">Privacy notice</Link>
          <Link href="/terms">Terms of use</Link>
          <Link href="/security">Security</Link>
          <a href="/.well-known/security.txt">security.txt</a>
        </nav>
      </div>
    </footer>
  );
}
