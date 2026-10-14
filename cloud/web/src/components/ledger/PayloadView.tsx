import { bytes } from "@/lib/format";
import type { Payload } from "@/lib/types";
import s from "./ledger.module.css";

function pretty(text: string): string {
  const t = text.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return text;
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    return text;
  }
}

/** Shows a payload's text when it was uploaded, and only its fingerprint when it wasn't. */
export function PayloadView({ payload, long = 12 }: { payload: Payload; long?: number }) {
  if (payload.text === null) {
    return (
      <p className={s.hidden}>
        <span>Text not uploaded</span>
        <span className={`${s.hash} mono`} title={payload.sha256}>
          {payload.sha256.slice(0, 12)}…, {bytes(payload.size)}
        </span>
      </p>
    );
  }
  const body = pretty(payload.text);
  const lines = body.split("\n").length;
  if (lines <= long) {
    return <pre className={s.payload}>{body}</pre>;
  }
  return (
    <details className={s.more}>
      <summary>
        <pre className={s.payload}>{body.split("\n").slice(0, long).join("\n")}</pre>
        <span className={s.moreLabel}>Show all {lines} lines</span>
      </summary>
      <pre className={s.payload}>{body}</pre>
    </details>
  );
}
