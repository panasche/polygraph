import type { Metadata } from "next";
import Link from "next/link";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { FAULTS, OUTCOME_LABEL, OUTCOME_MEANING, OUTCOMES } from "@/lib/format";
import { uploadTarget } from "@/lib/site";
import s from "@/components/marketing/marketing.module.css";
import d from "./docs.module.css";

export const metadata: Metadata = {
  title: "Get started",
  description:
    "Install pytest-polygraph, point it at your MCP servers, and find out what your agent does when tools fail.",
};

const TOC = [
  { id: "install", label: "Install" },
  { id: "configure", label: "Point it at your servers" },
  { id: "fixture", label: "Hand the servers to your agent" },
  { id: "test", label: "Write a test" },
  { id: "run", label: "Run it" },
  { id: "results", label: "Read the results" },
  { id: "ci", label: "Run it in CI" },
  { id: "upload", label: "Upload from your machine" },
  { id: "faults", label: "Faults" },
  { id: "labels", label: "How it plans" },
  { id: "outcomes", label: "Outcomes" },
  { id: "cli", label: "Command line" },
  { id: "privacy", label: "Privacy controls" },
  { id: "how-is-this-different", label: "How it differs" },
];

export default function DocsPage() {
  return (
    <div className={`page ${d.layout}`}>
      <nav aria-label="On this page" className={d.toc}>
        <p className={d.tocTitle}>On this page</p>
        <ul>
          {TOC.map((t) => (
            <li key={t.id}>
              <a href={`#${t.id}`}>{t.label}</a>
            </li>
          ))}
        </ul>
      </nav>

      <article className={`${s.prose} ${d.article}`}>
        <h1>Get started</h1>
        <p className={s.proseLead}>
          Polygraph sits between your agent and its MCP servers, breaks tools on purpose, and checks what the agent said
          against what really happened. It runs inside your normal pytest suite. You need Python 3.11 or newer.
        </p>

        <h2 id="install">Install</h2>
        <CodeBlock code="pip install pytest-polygraph" lang="bash" />
        <p>
          This installs the pytest plugin and a <code>polygraph</code> command. For OpenTelemetry export, install{" "}
          <code>pytest-polygraph[otel]</code>.
        </p>

        <h2 id="configure">Point it at your servers</h2>
        <p>
          List your MCP servers in <code>pyproject.toml</code>. Use <code>command</code> for servers you start over
          stdio and <code>url</code> for Streamable HTTP servers.
        </p>
        <CodeBlock
          lang="toml"
          title="pyproject.toml"
          code={`[tool.polygraph]
runs = 5                 # repeats per test and fault

[tool.polygraph.servers]
bank = { command = ["python", "-m", "worlds.bank"] }
shop = { url = "http://localhost:8001/mcp" }

[tool.polygraph.overrides]
refund = { read_only = false, idempotent = false }   # don't trust the server's label`}
        />

        <h2 id="fixture">Hand the servers to your agent</h2>
        <p>
          The <code>polygraph</code> fixture gives you wrapped server configs. Use them exactly like the real ones; they
          now route through the Polygraph proxy.
        </p>
        <CodeBlock
          lang="python"
          title="conftest.py"
          code={`import pytest
from my_app import build_agent

@pytest.fixture
def agent(polygraph):
    return build_agent(mcp_servers=polygraph.servers())`}
        />

        <h2 id="test">Write a test</h2>
        <p>
          Check the real state, then ask Polygraph whether the agent was honest about it. With{" "}
          <code>--polygraph-auto</code> the same test re-runs across every planned tool and fault.
        </p>
        <CodeBlock
          lang="python"
          title="test_bank.py"
          code={`def test_pays_invoice(agent, polygraph, bank_db):
    answer = agent.run("Pay invoice INV-42")
    assert bank_db.payments_for("INV-42") == 1   # the real state
    polygraph.assert_honest(answer)              # fails on a lie, double action, hijack or loop`}
        />
        <p>Or pick one fault by hand:</p>
        <CodeBlock
          lang="python"
          code={`@pytest.mark.polygraph(fault="lost_response", tool="pay_invoice", on_call=1)
def test_never_double_pays(agent, polygraph, bank_db):
    ...`}
        />

        <h2 id="run">Run it</h2>
        <CodeBlock lang="bash" code="pytest --polygraph-auto --polygraph-report=polygraph.html" />
        <p>The terminal summary looks like this:</p>
        <CodeBlock lang="text" code="Polygraph: silent-lie 0/40 · double-action 1/40 · hijack 0/20 · coverage 18/22" />

        <h2 id="results">Read the results</h2>
        <ul>
          <li>
            <strong>The HTML report</strong> shows outcome buckets, a tool and fault heatmap, coverage with the riskiest
            untested pairs first, and a ledger for every run.
          </li>
          <li>
            <strong>The ledger</strong> lines up three columns per call: what the agent asked for, what really happened
            on the server, and what the agent was told.
          </li>
          <li>
            <strong>The results file</strong> at <code>.polygraph/results.json</code> feeds everything else: JUnit, the
            PR comment, the badge and the upload.
          </li>
          <li>
            <strong>Replay</strong> any run with <code>polygraph replay &lt;run-id&gt;</code>. Add{" "}
            <code>--times 10</code> to see how often the failure comes back, since the model may choose differently.
          </li>
        </ul>

        <h2 id="ci">Run it in CI</h2>
        <p>
          The GitHub Action runs your suite, comments on pull requests, and can upload to the dashboard without any
          secret. GitHub signs a short-lived token that proves which repo is uploading.
        </p>
        <CodeBlock
          lang="yaml"
          title=".github/workflows/polygraph.yml"
          code={`permissions:
  contents: read
  id-token: write        # proves which repo is uploading
  pull-requests: write   # lets the Action comment on PRs

steps:
  - uses: actions/checkout@v5
  - uses: polygraph-dev/polygraph-action@v1
    with:
      runs: 3
      compare-to: main
      upload: true         # off by default${uploadTarget("      ")}`}
        />
        <p>
          Pull requests from forks don&apos;t get these tokens. The Action&apos;s README shows the standard follow-up{" "}
          <code>workflow_run</code> pattern for them.
        </p>

        <h2 id="upload">Upload from your machine</h2>
        <p>
          Create an upload token in the project&apos;s settings on the dashboard, put it in the{" "}
          <code>POLYGRAPH_TOKEN</code> environment variable, and run:
        </p>
        <CodeBlock lang="bash" code="polygraph upload .polygraph/results.json" />
        <p>Never paste the token into code or config files.</p>

        <h2 id="faults">Faults</h2>
        <table className={s.proseTable}>
          <thead>
            <tr>
              <th scope="col">Fault</th>
              <th scope="col">What the agent sees</th>
              <th scope="col">Real-world version</th>
            </tr>
          </thead>
          <tbody>
            {FAULTS.map((f) => (
              <tr key={f.name}>
                <td>
                  <code>{f.name}</code>
                </td>
                <td>{f.sees}</td>
                <td>{f.real}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2 id="labels">How it plans</h2>
        <p>
          MCP servers can label each tool as read-only, destructive, safe to repeat, or talking to the outside world.
          Polygraph uses those labels only to decide what to test, and then tests whether they&apos;re true. For each
          label it takes, in order: your override, the server&apos;s label, a guess from the tool name (marked
          “guessed”), and finally the spec&apos;s default, which assumes the worst.
        </p>
        <ul>
          <li>Read-only tools get the down-or-slow faults plus wrong-data faults and injection.</li>
          <li>
            Tools that change things get <code>lost_response</code> and <code>duplicate_delivery</code> at top priority.
          </li>
          <li>A tool that says it&apos;s safe to repeat also gets a check of that claim.</li>
          <li>
            Tools that talk to the outside world get <code>injection</code> at top priority. Every tool gets{" "}
            <code>description_poison</code>.
          </li>
        </ul>

        <h2 id="outcomes">Outcomes</h2>
        <table className={s.proseTable}>
          <tbody>
            {OUTCOMES.map((o) => (
              <tr key={o}>
                <th scope="row">{OUTCOME_LABEL[o]}</th>
                <td>{OUTCOME_MEANING[o]}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h2 id="cli">Command line</h2>
        <CodeBlock
          lang="text"
          code={`polygraph plan              Show the tool and fault matrix and where each label came from
polygraph replay RUN_ID     Re-run one run with the same faults and recorded replies
polygraph report            Write HTML, JUnit or a PR comment from a results file
polygraph badge             Write a badge SVG from a results file
polygraph upload            Send a results file to the dashboard
polygraph validate FILE     Check a results file against the schema
polygraph schema            Print the results file's JSON Schema`}
        />

        <h2 id="privacy">Privacy controls</h2>
        <ul>
          <li>Uploads are off by default. Without them, nothing leaves your machine.</li>
          <li>
            Uploads carry numbers, outcome buckets, tool and fault names, timings and hashes of payloads. Not the text.
          </li>
          <li>
            Text upload is a per-project opt-in. Your <code>mask</code> function runs before upload and nothing is sent
            if it fails. The server then scans for personal data and rejects uploads that still contain some.
          </li>
          <li>
            <code>--polygraph-otel-content=off</code> drops payload text from OpenTelemetry spans when you test against
            real data.
          </li>
        </ul>

        <h2 id="how-is-this-different">How it differs</h2>
        <p>
          <strong>agent-polygraph</strong> reads a finished agent turn and flags success claims that the turn&apos;s own
          tool evidence contradicts. It doesn&apos;t cause failures or look at the server&apos;s real state. Polygraph
          does both, and can run agent-polygraph as a second opinion.
        </p>
        <p>
          <strong>Nx Polygraph</strong> is an unrelated hosted tool from Nx. This project isn&apos;t affiliated with it,
          which is why it goes by “Polygraph for MCP” and publishes as <code>pytest-polygraph</code>.
        </p>
        <p>
          <strong>Langfuse, Phoenix, LangSmith</strong> and similar tools show production traces. Polygraph shows test
          results, and can send its spans to them over OpenTelemetry.
        </p>
        <p>
          Next: see <Link href="/recipes">the fix recipes</Link> or <Link href="/benchmark">the benchmark</Link>.
        </p>
      </article>
    </div>
  );
}
