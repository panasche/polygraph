"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { apiSend, ClientApiError } from "@/lib/api-client";
import s from "./dashboard.module.css";

export function SyncButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <span className={s.syncWrap}>
      <Button
        variant="secondary"
        small
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setMsg(null);
          try {
            await apiSend("POST", "/projects/sync");
            setMsg("Repos refreshed from GitHub.");
            router.refresh();
          } catch (e) {
            setMsg(e instanceof ClientApiError ? e.message : "GitHub couldn't be reached. Try again shortly.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Refreshing…" : "Refresh from GitHub"}
      </Button>
      <span role="status" aria-live="polite" className={s.faint}>
        {msg}
      </span>
    </span>
  );
}
