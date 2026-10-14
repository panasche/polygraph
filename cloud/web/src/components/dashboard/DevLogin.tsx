"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { apiSend, ClientApiError } from "@/lib/api-client";

/** Local development only. The API answers 404 to this route in every other environment. */
export function DevLogin({ next }: { next: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div style={{ display: "grid", gap: "0.4rem", justifyItems: "start" }}>
      <Button
        variant="quiet"
        small
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await apiSend("POST", "/auth/dev-login", { login: "dev-user" });
            window.location.assign(next);
          } catch (e) {
            setError(e instanceof ClientApiError ? e.message : "The local API isn't running on port 8000.");
            setBusy(false);
          }
        }}
      >
        {busy ? "Signing in…" : "Use a local dev account"}
      </Button>
      {error ? <span style={{ fontSize: "var(--t-12)", color: "var(--amber)" }}>{error}</span> : null}
    </div>
  );
}
