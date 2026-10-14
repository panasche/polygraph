"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import ui from "@/components/ui/ui.module.css";
import { apiSend, ClientApiError } from "@/lib/api-client";
import s from "./dashboard.module.css";

export function ConsentForm({ version, next }: { version: string; next: string }) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!agreed) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend("POST", "/me/consent", { privacy_version: version });
      window.location.assign(next);
    } catch (err) {
      setError(err instanceof ClientApiError ? err.message : "Your choice wasn't saved. Try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className={s.consentForm}>
      <label className={s.checkRow}>
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        <span>
          I&apos;ve read the <a href="/privacy">privacy notice</a> (version {version}) and agree to Polygraph storing my
          GitHub user id, username and avatar link to run my account.
        </span>
      </label>
      {error ? (
        <p className={`${ui.notice} ${ui.noticeBad}`} role="alert">
          {error}
        </p>
      ) : null}
      <div className={s.pageActions}>
        <Button type="submit" variant="primary" disabled={!agreed || busy}>
          {busy ? "Saving…" : "Agree and continue"}
        </Button>
        <Link href="/" className={s.quietLink}>
          Not now
        </Link>
      </div>
    </form>
  );
}
