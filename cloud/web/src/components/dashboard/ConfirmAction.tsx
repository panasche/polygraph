"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "@/components/ui/Button";
import ui from "@/components/ui/ui.module.css";
import { apiSend, ClientApiError } from "@/lib/api-client";
import s from "./dashboard.module.css";

/**
 * A destructive action with an inline confirmation step. When `requireText`
 * is set, the person must type it before the action unlocks.
 */
export function ConfirmAction({
  label,
  confirmLabel,
  description,
  path,
  method = "DELETE",
  redirectTo,
  requireText,
  hardNavigate,
}: {
  label: string;
  confirmLabel: string;
  description: string;
  path: string;
  method?: "DELETE" | "POST";
  redirectTo: string;
  requireText?: string;
  hardNavigate?: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await apiSend(method, path);
      if (hardNavigate) {
        window.location.assign(redirectTo);
        return;
      }
      router.push(redirectTo);
      router.refresh();
    } catch (e) {
      setError(e instanceof ClientApiError ? e.message : "Something went wrong. Nothing was deleted.");
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button variant="danger" small onClick={() => setOpen(true)}>
        {label}
      </Button>
    );
  }

  const locked = requireText !== undefined && typed !== requireText;
  return (
    <div className={s.confirm} role="group" aria-label={label}>
      <p>{description}</p>
      {requireText !== undefined ? (
        <div className={ui.field}>
          <label htmlFor={inputId} className={ui.hint}>
            Type <code>{requireText}</code> to confirm
          </label>
          <input
            id={inputId}
            className={ui.input}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      ) : null}
      {error ? (
        <p className={`${ui.notice} ${ui.noticeBad}`} role="alert">
          {error}
        </p>
      ) : null}
      <div className={s.confirmActions}>
        <Button variant="danger" small onClick={run} disabled={locked || busy}>
          {busy ? "Deleting…" : confirmLabel}
        </Button>
        <Button
          variant="quiet"
          small
          onClick={() => {
            setOpen(false);
            setTyped("");
            setError(null);
          }}
          disabled={busy}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
