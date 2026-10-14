import type { Metadata } from "next";
import s from "@/components/marketing/marketing.module.css";
import { HOSTING } from "@/lib/hosting";
import { SECURITY_EMAIL, SITE_URL } from "@/lib/site";

export const metadata: Metadata = { title: "Security" };

export default function SecurityPage() {
  return (
    <article className={`page ${s.prose}`}>
      <h1>Security</h1>
      <p className={s.proseLead}>
        If you find a security problem in Polygraph or Polygraph Cloud, please tell us privately first. We&apos;ll work
        with you to fix it and credit you if you&apos;d like.
      </p>

      <h2>How to report</h2>
      <p>
        Email <a href={`mailto:${SECURITY_EMAIL}`}>{SECURITY_EMAIL}</a>, or open a private security advisory on the
        GitHub repository. Include what you found, how to reproduce it, and what an attacker could do with it.
        We&apos;ll acknowledge your report within 3 working days.
      </p>

      <h2>In scope</h2>
      <ul>
        <li>{new URL(SITE_URL).host} and its API</li>
        <li>The pytest-polygraph package and the polygraph-action GitHub Action</li>
        <li>Upload authentication, project visibility, and data isolation between projects</li>
      </ul>

      <h2>Please don&apos;t</h2>
      <ul>
        <li>Access, change or delete data that isn&apos;t yours. Use your own test projects.</li>
        <li>Run denial-of-service tests, spam uploads, or automated scanning that degrades the service.</li>
        <li>Use social engineering or physical attacks.</li>
      </ul>
      <p>We won&apos;t pursue legal action against good-faith research that follows these rules.</p>

      <h2>How we protect data</h2>
      <ul>
        <li>Uploads from GitHub Actions use short-lived OIDC tokens, so there&apos;s no long-lived secret to leak.</li>
        <li>Upload tokens are shown once and stored only as hashes. Sessions are stored as hashes too.</li>
        <li>The GitHub App asks only for read-only metadata. It never reads code and never asks for email.</li>
        <li>
          Data stays in {HOSTING.short}, encrypted with TLS in transit and at rest {HOSTING.atRest}.
        </li>
        <li>Secrets never go in URLs, which is why private projects don&apos;t get a badge.</li>
        <li>
          {HOSTING.waf
            ? "A web application firewall rate-limits uploads and requests."
            : "The API rate-limits uploads per project and per network address."}
        </li>
      </ul>
    </article>
  );
}
