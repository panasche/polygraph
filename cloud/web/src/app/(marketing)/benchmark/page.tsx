import type { Metadata } from "next";
import { Suspense } from "react";
import { LeaderboardTable } from "@/components/marketing/LeaderboardTable";
import { apiPublic } from "@/lib/api-server";
import type { Leaderboard } from "@/lib/types";
import s from "@/components/marketing/marketing.module.css";

export const metadata: Metadata = {
  title: "Benchmark",
  description:
    "Four agent frameworks, the same model and the same faults. How often does each one say done when the database says no?",
};

const THREATS: { threat: string; means: string; response: string }[] = [
  {
    threat: "Fake worlds",
    means: "Real services are messier than three small test servers.",
    response: "The worlds are small but realistic, with real SQLite state. A later release audits real open-source servers in a sandbox.",
  },
  {
    threat: "Default settings",
    means: "Teams may configure their framework better than the defaults.",
    response: "A second column uses settings the framework maintainers suggest.",
  },
  {
    threat: "One main model",
    means: "Other models may behave differently.",
    response: "The three headline faults are re-run with a stronger model. A full model comparison comes later.",
  },
  {
    threat: "Randomness",
    means: "The same run can end differently twice.",
    response: "Many repeats, 95% confidence intervals and pass^k, which asks whether every repeat passed.",
  },
  {
    threat: "Judge mistakes",
    means: "The judge may misread whether the agent claimed success.",
    response: "It answers one narrow question, a sample is checked by hand, and the agreement rate is published.",
  },
  {
    threat: "Author bias",
    means: "The same person built the tool and the tasks.",
    response: "Predictions are committed before the paid runs, the code is open, and maintainers review results first.",
  },
];

export default function BenchmarkPage() {
  return (
    <>
      <article className={`page ${s.prose}`}>
        <h1>The benchmark</h1>
        <p className={s.proseLead}>
          LangGraph, OpenAI Agents SDK, Pydantic AI and smolagents, run with the same model, the same system prompt,
          the same tools and the same faults. The headline number is the silent-lie rate: how often an agent says
          “done” when the database says it isn&apos;t.
        </p>
      </article>

      <section className="page" aria-labelledby="results-title" style={{ paddingBottom: "1rem" }}>
        <h2 id="results-title" className="visually-hidden">
          Results
        </h2>
        <Suspense fallback={<p>Loading results…</p>}>
          <Results />
        </Suspense>
      </section>

      <article className={`page ${s.prose}`}>
        <h2>Three fake worlds</h2>
        <p>
          Each world is a small MCP server with its own SQLite database. All data is invented, like “Customer A” and
          invoice INV-42, and no real people appear.
        </p>
        <table className={s.proseTable}>
          <thead>
            <tr>
              <th scope="col">World</th>
              <th scope="col">Tools</th>
              <th scope="col">Headline faults</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Bank</td>
              <td>
                <code>get_invoice</code>, <code>pay_invoice</code>, <code>refund</code>
              </td>
              <td>
                <code>lost_response</code>, <code>duplicate_delivery</code>
              </td>
            </tr>
            <tr>
              <td>Shop</td>
              <td>
                <code>search_products</code>, <code>order_status</code>, <code>track_shipment</code>,{" "}
                <code>place_order</code>
              </td>
              <td>
                <code>partial</code>, <code>stale</code>, <code>schema_drift</code>
              </td>
            </tr>
            <tr>
              <td>Inbox</td>
              <td>
                <code>read_email</code>, <code>send_email</code>, <code>create_event</code>
              </td>
              <td>
                <code>injection</code>, <code>description_poison</code>
              </td>
            </tr>
          </tbody>
        </table>
        <p>
          The tool labels are mixed on purpose. Some tools are labelled, some aren&apos;t, and one is labelled wrongly:
          <code> refund</code> claims it&apos;s safe to repeat, and it isn&apos;t. Polygraph should catch the server
          lying too.
        </p>

        <h2>What counts</h2>
        <ul>
          <li>Every task has a Python check of the final database state, like “INV-42 is paid exactly once”.</li>
          <li>The planner decides which tool and fault pairs apply, so only sensible combinations run.</li>
          <li>
            Every rate comes with a 95% Wilson interval. A rate from a handful of runs shows a wide interval, and the
            table never hides it.
          </li>
          <li>Each run records its token use, so the write-up can say what reproducing it costs in rupees.</li>
        </ul>

        <h2>How we keep it honest</h2>
        <ul>
          <li>Predictions are written down and tagged in git before any paid run.</li>
          <li>
            Everything is pinned: the lockfile, the Docker image digest, the git SHA, the model id and snapshot date,
            the framework and MCP SDK versions, and the protocol version each framework spoke.
          </li>
          <li>
            Each framework&apos;s maintainers get the results and the steps to reproduce them before publication, with
            a fixed review window announced up front.
          </li>
          <li>
            An optional transcript-only check (agent-polygraph&apos;s <code>verify()</code>) runs alongside, to show
            where reading the transcript agrees or disagrees with the server&apos;s real state.
          </li>
        </ul>

        <h2>Threats to validity</h2>
        <table className={s.proseTable}>
          <thead>
            <tr>
              <th scope="col">Threat</th>
              <th scope="col">What it means</th>
              <th scope="col">What we do</th>
            </tr>
          </thead>
          <tbody>
            {THREATS.map((t) => (
              <tr key={t.threat}>
                <td>{t.threat}</td>
                <td>{t.means}</td>
                <td>{t.response}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2>Related work</h2>
        <p>
          This builds on ideas from mcp-storm (seeded faults and predictions written in advance), mcpfault (checking
          the server&apos;s real state), agent-polygraph (transcript-level lie detection), MCPTox and AgentDojo
          (tool poisoning and injection), and τ-bench (the pass^k metric).
        </p>
      </article>
    </>
  );
}

async function Results() {
  const board = await apiPublic<Leaderboard>("/leaderboard");
  return <LeaderboardTable board={board} />;
}
