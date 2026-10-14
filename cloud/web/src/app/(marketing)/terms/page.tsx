import type { Metadata } from "next";
import Link from "next/link";
import s from "@/components/marketing/marketing.module.css";

export const metadata: Metadata = { title: "Terms of use" };

// Before launch: have these terms reviewed by a lawyer.
export default function TermsPage() {
  return (
    <article className={`page ${s.prose}`}>
      <h1>Terms of use</h1>
      <p className={s.proseMeta}>Version 2026-10-13</p>
      <p className={s.proseLead}>
        Polygraph is free and open source. These terms cover the hosted dashboard, Polygraph Cloud. The software itself
        is covered by the Apache 2.0 licence.
      </p>

      <h2>The service</h2>
      <ul>
        <li>Polygraph Cloud stores and shows test results you upload. It never runs your agent or calls a model.</li>
        <li>It&apos;s free, with no paid tier and no uptime promise. We publish a status page and do our best.</li>
        <li>We may change or stop the service. If we stop it, we&apos;ll give notice so you can export your data.</li>
      </ul>

      <h2>Fair use</h2>
      <p>
        Uploads are limited in size, frequency and retention so the service stays free for everyone. The current limits
        are on the home page. Don&apos;t try to get around them, upload anything you don&apos;t have the right to
        upload, use the service to attack anyone, or upload personal data.
      </p>

      <h2>Your content</h2>
      <p>
        You keep whatever rights you have in what you upload. You allow us to store and display it as the service
        needs: publicly for public projects with public results, and only to repo members otherwise.
      </p>

      <h2>Security research</h2>
      <p>
        Good-faith research is welcome under our <Link href="/security">security policy</Link>.
      </p>

      <h2>No warranty</h2>
      <p>
        The service is provided as is. Benchmark results describe specific configurations at a specific time and are not
        a guarantee about any framework, model or product. To the extent the law allows, we&apos;re not liable for
        indirect or consequential losses arising from using it.
      </p>

      <h2>Law</h2>
      <p>These terms are governed by the laws of India.</p>
    </article>
  );
}
