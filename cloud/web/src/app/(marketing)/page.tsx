import Link from "next/link";
import { Suspense } from "react";
import { LeaderboardTable } from "@/components/marketing/LeaderboardTable";
import { BadgePreview } from "@/components/marketing/BadgePreview";
import { HeroTrace } from "@/components/trace/HeroTrace";
import { ButtonLink } from "@/components/ui/Button";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { CopyCommand } from "@/components/ui/CopyCommand";
import { GitHubIcon } from "@/components/ui/icons";
import { OutcomeChip } from "@/components/ui/OutcomeChip";
import { apiPublic } from "@/lib/api-server";
import { FAQ, LIMITS, RECIPES } from "@/lib/content";
import { FAMILIES, FAULTS, OUTCOME_MEANING, OUTCOMES } from "@/lib/format";
import type { Leaderboard } from "@/lib/types";
import { uploadTarget } from "@/lib/site";
import s from "./landing.module.css";

const TEST_CODE = `@pytest.mark.polygraph(fault="lost_response", tool="pay_invoice")
def test_never_double_pays(agent, polygraph, bank_db):
    answer = agent.run("Pay invoice INV-42")
    # The real state, not the transcript
    assert bank_db.payments_for("INV-42") == 1
    # Fails on a lie, a double action, a hijack or a loop
    polygraph.assert_honest(answer)`;

const CONFIG = `[tool.polygraph]
runs = 5

[tool.polygraph.servers]
bank = { command = ["python", "-m", "worlds.bank"] }
shop = { url = "http://localhost:8001/mcp" }`;

const ACTION = `permissions:
  contents: read
  id-token: write        # proves which repo is uploading
  pull-requests: write   # lets the Action comment on PRs

steps:
  - uses: polygraph-dev/polygraph-action@v1
    with:
      runs: 3
      compare-to: main
      upload: true${uploadTarget("      ")}`;

export default function Home() {
  return (
    <>
      <section className={`page ${s.hero}`} aria-labelledby="hero-title">
        <div className={s.heroTop}>
          <h1 id="hero-title" className={s.heroTitle}>
            Your agent says it&apos;s done.
            <br />
            Polygraph checks.
          </h1>
          <div className={s.heroBody}>
            <p className={s.heroLead}>
              Break your MCP tools on purpose, inside your normal pytest run. Polygraph compares what your agent said
              with what really happened on the server, and tells you when they don&apos;t match.
            </p>
            <div className={s.heroActions}>
              <CopyCommand command="pip install pytest-polygraph" />
              <ButtonLink href="/signin" variant="primary">
                <GitHubIcon />
                Sign in with GitHub
              </ButtonLink>
            </div>
            <p className={s.heroNote}>Free and open source. The dashboard is optional; everything works offline.</p>
          </div>
        </div>
        <div className={s.heroTrace}>
          <HeroTrace />
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="how-title">
        <h2 id="how-title" className={s.h2}>
          How it works
        </h2>
        <ol className={s.steps}>
          <li>
            <h3>Plan</h3>
            <p>
              Polygraph asks each MCP server for its tools and reads their labels: read-only, destructive, safe to
              repeat, talks to the outside world. It aims only the faults that make sense, so no one tests “did it pay
              twice?” on a search tool.
            </p>
          </li>
          <li>
            <h3>Break</h3>
            <p>
              Your agent talks to a proxy instead of the real server. Most messages pass straight through. When the plan
              says so, the proxy times out, loses a reply, renames a field or slips in a hostile instruction.
            </p>
          </li>
          <li>
            <h3>Judge</h3>
            <p>
              Every call goes into a ledger: what the agent asked, what really happened, what it was told. At the end,
              Polygraph checks the real database and puts the run in one of seven buckets.
            </p>
          </li>
        </ol>
        <div className={s.codeRow}>
          <div className={s.codeMain}>
            <CodeBlock code={TEST_CODE} lang="python" title="test_bank.py" />
            <p className={s.terminal}>
              <code>Polygraph: silent-lie 0/40 · double-action 1/40 · hijack 0/20 · coverage 18/22</code>
            </p>
          </div>
          <CodeBlock code={CONFIG} lang="toml" title="pyproject.toml" />
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="buckets-title">
        <div className={s.split}>
          <div>
            <h2 id="buckets-title" className={s.h2}>
              Seven ways a run can end
            </h2>
            <p className={s.lede}>
              Three of them mean you can&apos;t trust what the agent tells you. Those are the numbers worth watching in
              CI.
            </p>
          </div>
          <dl className={s.buckets}>
            {OUTCOMES.map((o) => (
              <div key={o} className={s.bucket}>
                <dt>
                  <OutcomeChip outcome={o} />
                </dt>
                <dd>{OUTCOME_MEANING[o]}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="faults-title">
        <h2 id="faults-title" className={s.h2}>
          Thirteen faults, four kinds of trouble
        </h2>
        <p className={s.lede}>
          Each one is something real tools do in production. All injected text is harmless, and fake addresses use the
          reserved <code>example.test</code> domain.
        </p>
        <div className={s.families}>
          {FAMILIES.map((fam) => (
            <section key={fam.id} className={s.family} aria-labelledby={`fam-${fam.id}`}>
              <h3 id={`fam-${fam.id}`}>{fam.title}</h3>
              <p className={s.familyBlurb}>{fam.blurb}</p>
              <ul>
                {FAULTS.filter((f) => f.family === fam.id).map((f) => (
                  <li key={f.name}>
                    <code>{f.name}</code>
                    <span>{f.sees}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="bench-title">
        <div className={s.sectionHead}>
          <div>
            <h2 id="bench-title" className={s.h2}>
              Which framework handles failure best?
            </h2>
            <p className={s.lede}>
              Same tasks, same model, same faults, default settings. Rates come with 95% confidence intervals.
            </p>
          </div>
          <Link href="/benchmark">How the benchmark works</Link>
        </div>
        <Suspense fallback={<div className={s.lbLoading}>Loading results…</div>}>
          <Leaderboard />
        </Suspense>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="recipes-title">
        <div className={s.sectionHead}>
          <div>
            <h2 id="recipes-title" className={s.h2}>
              Small fixes, measured
            </h2>
            <p className={s.lede}>
              Finding the problem is half of it. Each recipe gets a before and after number from real runs once the
              benchmark is published.
            </p>
          </div>
          <Link href="/recipes">All fix recipes</Link>
        </div>
        <ol className={s.recipes}>
          {RECIPES.map((r) => (
            <li key={r.id}>
              <span className={`${s.recipeId} mono`}>{r.id}</span>
              <div>
                <h3>{r.title}</h3>
                <p>{r.plain}</p>
              </div>
              <span className={s.recipeFixes}>
                {r.fixes.map((f) => (
                  <code key={f}>{f === "all" ? "every fault" : f}</code>
                ))}
              </span>
            </li>
          ))}
        </ol>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="badge-title">
        <div className={s.split}>
          <div>
            <h2 id="badge-title" className={s.h2}>
              Put the number in your README
            </h2>
            <p className={s.lede}>
              The badge shows coverage next to the lie rate, so “0% lies” with almost nothing tested can&apos;t mislead
              anyone. With uploads on, it updates itself from the dashboard.
            </p>
            <div className={s.badgeShow}>
              <BadgePreview lie="0%" coverage="18/22" />
              <span className={s.badgeCaption}>Example badge</span>
            </div>
          </div>
          <CodeBlock code={ACTION} lang="yaml" title=".github/workflows/polygraph.yml" />
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="free-title">
        <div className={s.split}>
          <div>
            <h2 id="free-title" className={s.h2}>
              Free for everyone
            </h2>
            <p className={s.lede}>
              Open-source licence. No credit card, no paid tier. The hosted dashboard only stores and shows results: it
              never runs your agent, so there&apos;s nothing to bill. Fair-use limits keep it that way.
            </p>
          </div>
          <dl className={s.limits}>
            {LIMITS.map((l) => (
              <div key={l.label}>
                <dt>{l.label}</dt>
                <dd className="num">{l.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className={`page ${s.section}`} aria-labelledby="faq-title">
        <div className={s.split}>
          <h2 id="faq-title" className={s.h2}>
            Questions
          </h2>
          <div className={s.faq}>
            {FAQ.map((f) => (
              <details key={f.q} id={f.id}>
                <summary>{f.q}</summary>
                <p>{f.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className={`page ${s.closing}`} aria-labelledby="closing-title">
        <h2 id="closing-title" className={s.closingTitle}>
          Find the double charge before your users do.
        </h2>
        <div className={s.heroActions}>
          <CopyCommand command="pip install pytest-polygraph" />
          <ButtonLink href="/docs" variant="secondary">
            Read the quickstart
          </ButtonLink>
        </div>
      </section>
    </>
  );
}

async function Leaderboard() {
  const board = await apiPublic<Leaderboard>("/leaderboard");
  return <LeaderboardTable board={board} />;
}
