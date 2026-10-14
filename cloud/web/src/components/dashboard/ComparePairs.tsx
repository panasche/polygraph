import type { ComparePair } from "@/lib/types";
import s from "./dashboard.module.css";

type Side = { runs: number; bad: number } | null;

const rate = (x: Side) => (x && x.runs ? x.bad / x.runs : null);

function Cell({ side }: { side: Side }) {
  return side?.runs ? (
    <>
      {side.bad} of {side.runs}
    </>
  ) : (
    <span className={s.faint}>not run</span>
  );
}

function Rows({ pairs }: { pairs: ComparePair[] }) {
  return (
    <div className={s.tableWrap}>
      <table className={s.table}>
        <thead>
          <tr>
            <th scope="col">Pair</th>
            <th scope="col" className={s.right}>
              Base bad
            </th>
            <th scope="col" className={s.right}>
              Compare bad
            </th>
          </tr>
        </thead>
        <tbody>
          {pairs.map((p) => {
            const b = rate(p.base);
            const h = rate(p.head);
            const tone = b !== null && h !== null ? (h > b ? s.textBad : h < b ? s.textGood : "") : "";
            return (
              <tr key={`${p.server}/${p.tool}/${p.fault}`}>
                <th scope="row">
                  <code>
                    {p.tool} × {p.fault}
                  </code>
                </th>
                <td className={`num ${s.right}`}>
                  <Cell side={p.base} />
                </td>
                <td className={`num ${s.right} ${tone}`}>
                  <Cell side={p.head} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Pairs whose bad-outcome rate moved, largest move first, regressions before
 * improvements of the same size. Unchanged pairs fold away underneath.
 */
export function ComparePairs({ pairs }: { pairs: ComparePair[] }) {
  if (pairs.length === 0) return <p className={s.notice}>Neither report tested any tool and fault pairs.</p>;
  const delta = (p: ComparePair) => {
    const b = rate(p.base);
    const h = rate(p.head);
    return b === null || h === null ? null : h - b;
  };
  const changed = pairs
    .filter((p) => delta(p) === null || Math.abs(delta(p)!) > 1e-9)
    .sort((a, b) => {
      const da = delta(a);
      const db = delta(b);
      if (da === null || db === null) return da === null ? 1 : -1;
      return Math.abs(db) - Math.abs(da) || db - da;
    });
  const same = pairs.filter((p) => !changed.includes(p));

  return (
    <div className={s.section}>
      {changed.length > 0 ? (
        <Rows pairs={changed} />
      ) : (
        <p className={s.notice}>No tool and fault pair changed between these reports.</p>
      )}
      {same.length > 0 ? (
        <details className={s.unchanged}>
          <summary>
            {same.length} unchanged {same.length === 1 ? "pair" : "pairs"}
          </summary>
          <Rows pairs={same} />
        </details>
      ) : null}
    </div>
  );
}
