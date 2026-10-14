import type { LedgerEntry } from "@/lib/types";
import { PayloadView } from "./PayloadView";
import s from "./ledger.module.css";

function forwardedText(n: number) {
  if (n === 0) return "Never reached the server";
  if (n === 1) return "Reached the server once";
  if (n === 2) return "Reached the server twice";
  return `Reached the server ${n} times`;
}

/**
 * Three aligned columns per call: what the agent asked for, what really
 * happened on the server, and what the agent was told.
 */
export function LedgerView({ entries }: { entries: LedgerEntry[] }) {
  const calls = entries.filter((e) => e.method !== "tools/list" || e.fault !== null);
  const hiddenLists = entries.length - calls.length;
  return (
    <div className={s.ledger}>
      <div className={s.head} aria-hidden="true">
        <span />
        <span className={s.colAsked}>Asked</span>
        <span className={s.colHappened}>Happened</span>
        <span className={s.colTold}>Told</span>
      </div>
      <ol className={s.rows}>
        {calls.map((e) => {
          const twice = e.happened.forwarded > 1;
          return (
            <li key={e.seq} className={`${s.row} ${e.fault ? s.rowFault : ""}`}>
              <div className={s.meta}>
                <span className={`${s.seq} num`}>{e.seq + 1}</span>
                <span className={s.tool}>
                  <code>{e.tool ?? e.method}</code>
                  <span className={s.server}>{e.server}</span>
                </span>
                {e.fault ? <code className={s.faultTag}>{e.fault}</code> : null}
                <span className={`${s.latency} num`}>{Math.round(e.latency_ms)} ms</span>
              </div>

              <div className={s.cell}>
                <span className={s.cellLabel}>Asked</span>
                <PayloadView payload={e.asked} />
              </div>

              <div className={s.cell}>
                <span className={s.cellLabel}>Happened</span>
                <p className={`${s.fact} ${twice ? s.factBad : ""}`}>{forwardedText(e.happened.forwarded)}</p>
                {e.happened.note ? <p className={s.note}>{e.happened.note}</p> : null}
                {e.happened.server_result ? (
                  <>
                    {e.happened.server_is_error ? <p className={s.errTag}>Server returned an error</p> : null}
                    <PayloadView payload={e.happened.server_result} />
                  </>
                ) : null}
              </div>

              <div className={s.cell}>
                <span className={s.cellLabel}>Told</span>
                {e.told === null ? (
                  <p className={s.fact}>No reply</p>
                ) : (
                  <>
                    {e.told_is_error ? <p className={s.errTag}>Error</p> : null}
                    <PayloadView payload={e.told} />
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {hiddenLists > 0 ? (
        <p className={s.footnote}>
          {hiddenLists} tool list {hiddenLists === 1 ? "request" : "requests"} passed through untouched and are not
          shown.
        </p>
      ) : null}
    </div>
  );
}
