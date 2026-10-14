import Link from "next/link";
import { FAULT_ORDER, OUTCOME_LABEL, OUTCOMES } from "@/lib/format";
import type { CoveragePair, FaultName, HeatCell, ToolInfo } from "@/lib/types";
import s from "./charts.module.css";

type Props = {
  cells: HeatCell[];
  pairs: CoveragePair[];
  tools: ToolInfo[];
  /** Base href for a cell link; tool and fault are added as query params. */
  runsHref?: string;
};

/**
 * Tool × fault grid. Each cell shows bad outcomes over runs. Cells that apply
 * but weren't tested are outlined; cells that don't apply stay blank, which
 * mirrors how the planner aims faults.
 */
export function Heatmap({ cells, pairs, tools, runsHref }: Props) {
  const key = (server: string, tool: string, fault: string) => `${server}/${tool}/${fault}`;
  const cellMap = new Map(cells.map((c) => [key(c.server, c.tool, c.fault), c]));
  const pairMap = new Map(pairs.map((p) => [key(p.server, p.tool, p.fault), p]));

  const rowKeys = new Map<string, { server: string; tool: string }>();
  for (const t of tools) rowKeys.set(`${t.server}/${t.name}`, { server: t.server, tool: t.name });
  for (const p of pairs) rowKeys.set(`${p.server}/${p.tool}`, { server: p.server, tool: p.tool });
  for (const c of cells) rowKeys.set(`${c.server}/${c.tool}`, { server: c.server, tool: c.tool });
  const rows = [...rowKeys.values()].sort((a, b) => a.server.localeCompare(b.server) || a.tool.localeCompare(b.tool));

  const usedFaults = new Set<FaultName>([...cells.map((c) => c.fault), ...pairs.map((p) => p.fault)]);
  const cols = FAULT_ORDER.filter((f) => usedFaults.has(f));

  if (rows.length === 0 || cols.length === 0) {
    return <p className={s.emptyNote}>No tool and fault pairs were planned in this report.</p>;
  }

  return (
    <div className={s.heatWrap}>
      <table className={s.heat}>
        <caption className="visually-hidden">
          Bad outcomes per tool and fault. Blank cells don&apos;t apply to that tool.
        </caption>
        <thead>
          <tr>
            <th scope="col" className={s.heatCorner}>
              Tool
            </th>
            {cols.map((f) => (
              <th key={f} scope="col" className={s.heatCol}>
                <span className="mono">{f}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.server}/${r.tool}`}>
              <th scope="row" className={s.heatRow}>
                <span className={s.heatServer}>{r.server}/</span>
                <span className="mono">{r.tool}</span>
              </th>
              {cols.map((f) => {
                const c = cellMap.get(key(r.server, r.tool, f));
                const p = pairMap.get(key(r.server, r.tool, f));
                if (c && c.runs > 0) {
                  const rate = c.bad / c.runs;
                  const title = `${r.tool} × ${f}: ${c.bad} bad of ${c.runs} runs. ${OUTCOMES.filter(
                    (o) => (c.outcomes[o] ?? 0) > 0,
                  )
                    .map((o) => `${OUTCOME_LABEL[o]} ${c.outcomes[o]}`)
                    .join(", ")}`;
                  const content = (
                    <>
                      <span className="num">
                        {c.bad}/{c.runs}
                      </span>
                      {p?.priority === "top" ? <span className={s.heatTop} aria-label="top priority" /> : null}
                    </>
                  );
                  return (
                    <td
                      key={f}
                      className={`${s.heatCell} ${c.bad > 0 ? s.heatBad : s.heatClean}`}
                      style={c.bad > 0 ? { ["--heat" as string]: `${Math.round(18 + rate * 72)}%` } : undefined}
                      title={title}
                    >
                      {runsHref ? (
                        <Link
                          href={`${runsHref}?tool=${encodeURIComponent(r.tool)}&fault=${f}`}
                          aria-label={title}
                          className={s.heatLink}
                        >
                          {content}
                        </Link>
                      ) : (
                        content
                      )}
                    </td>
                  );
                }
                if (p) {
                  return (
                    <td key={f} className={`${s.heatCell} ${s.heatUntested}`} title={`${r.tool} × ${f}: not tested yet`}>
                      <span className="visually-hidden">not tested</span>
                      {p.priority === "top" ? <span className={s.heatTop} aria-label="top priority" /> : null}
                    </td>
                  );
                }
                return (
                  <td key={f} className={`${s.heatCell} ${s.heatNa}`}>
                    <span className="visually-hidden">does not apply</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <ul className={s.heatLegend}>
        <li>
          <span className={`${s.heatKey} ${s.heatClean}`} /> Tested, no bad outcomes
        </li>
        <li>
          <span className={`${s.heatKey} ${s.heatBad}`} style={{ ["--heat" as string]: "60%" }} /> Bad outcomes, darker is
          worse
        </li>
        <li>
          <span className={`${s.heatKey} ${s.heatUntested}`} /> Applies, not tested yet
        </li>
        <li>
          <span className={s.heatTopKey} /> Top priority pair
        </li>
      </ul>
    </div>
  );
}
