"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { CopyCommand } from "@/components/ui/CopyCommand";
import { RelTime } from "@/components/ui/RelTime";
import ui from "@/components/ui/ui.module.css";
import { apiSend, ClientApiError } from "@/lib/api-client";
import type { UploadToken } from "@/lib/types";
import s from "./dashboard.module.css";

export function TokenManager({ tokens, apiPath }: { tokens: UploadToken[]; apiPath: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<{ name: string; token: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    setError(null);
    try {
      const res = await apiSend<UploadToken & { token: string }>("POST", `${apiPath}/tokens`, { name: n });
      setCreated({ name: res.name, token: res.token });
      setName("");
      router.refresh();
    } catch (err) {
      setError(err instanceof ClientApiError ? err.message : "The token wasn't created.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    setRevoking(id);
    setError(null);
    try {
      await apiSend("DELETE", `${apiPath}/tokens/${id}`);
      setCreated(null); // never leave a revoked token's value on screen
      router.refresh();
    } catch (err) {
      setError(err instanceof ClientApiError ? err.message : "The token wasn't revoked.");
    } finally {
      setRevoking(null);
    }
  }

  return (
    <div className={s.tokens}>
      {created ? (
        <div className={`${ui.notice} ${ui.noticeWarn}`} role="status">
          <p>
            <strong>Copy the token for “{created.name}” now.</strong> It won&apos;t be shown again. Store it as{" "}
            <code>POLYGRAPH_TOKEN</code> in your environment, never in code or config files.
          </p>
          <div className={s.tokenReveal}>
            <CopyCommand command={created.token} prompt={false} />
            <Button variant="quiet" small onClick={() => setCreated(null)}>
              I&apos;ve stored it
            </Button>
          </div>
        </div>
      ) : null}

      {tokens.length === 0 ? (
        <p className={s.faint}>No upload tokens. GitHub Actions uploads don&apos;t need one.</p>
      ) : (
        <ul className={s.tokenList}>
          {tokens.map((t) => (
            <li key={t.id}>
              <div>
                <p className={s.tokenName}>{t.name}</p>
                <p className={s.faint}>
                  <code>{t.prefix}…</code> created by {t.created_by} <RelTime iso={t.created_at} />. Last used{" "}
                  <RelTime iso={t.last_used_at} />.
                </p>
              </div>
              <Button variant="danger" small onClick={() => revoke(t.id)} disabled={revoking !== null}>
                {revoking === t.id ? "Revoking…" : "Revoke"}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={create} className={s.tokenForm}>
        <label className={ui.field}>
          <span className={ui.label}>New token name</span>
          <input
            className={ui.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="For example, laptop"
            maxLength={64}
            autoComplete="off"
          />
        </label>
        <Button type="submit" variant="secondary" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create token"}
        </Button>
      </form>
      {error ? (
        <p className={`${ui.notice} ${ui.noticeBad}`} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
