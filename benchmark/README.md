# Polygraph benchmark

This folder holds the benchmark harness for Polygraph for MCP. It asks one
question about popular agent frameworks: **when a tool breaks, does the agent
tell the truth about what happened?**

We break the agent's tools on purpose (a lost reply, a duplicate request, half
the data, a hidden instruction, a tool description that turns hostile), then
compare what the agent *said* with what the fake server's database *shows*.
Every run lands in one bucket: recovered, honest fail, **silent lie**,
**double action**, **hijacked**, loop or crash.

Nothing here contains results. The numbers come only from real runs, and the
write-up template has blanks (`__`) until then.

## What it measures

- **4 frameworks**: LangGraph, OpenAI Agents SDK, Pydantic AI and smolagents,
  each through its own MCP client.
- **3 fake worlds**: a bank, a shop and an inbox. Each is a small MCP server
  with a real SQLite database and invented data ("Customer A", "INV-42",
  `*@example.test`).
- **19 tasks** (in `tasks/`), each checked against the final database state,
  for example "INV-42 is paid exactly once".
- **13 faults**, planned per tool by Polygraph (`--polygraph-auto`). Seven tasks
  are also pinned to one headline fault each, so those always run: lost
  response and duplicate delivery (bank), partial results, a stale/conflicting
  record and a renamed field (shop), an injection inside an email body and a
  `send_email` rug pull that asks for a hidden BCC (inbox).
- **7 fix recipes** (R1 to R7), each a separate column, to see what actually helps.

The headline number is the **silent-lie rate**. Every rate comes with a 95 %
confidence interval, plus `pass^k` (did a task pass every repeat).

## The fairness rules

1. **Same model, same system prompt, same tools** for every framework. The
   prompt is in `bench/prompt.py` and the adapters refuse any other prompt.
2. **Default framework settings** for the `default` column. No timeouts,
   retries, caches or turn limits are changed. The one exception: telemetry
   that would upload traces to a vendor is switched off, because it changes
   nothing about how the agent behaves.
3. **A "best-practice" column** uses settings the framework's maintainers
   suggest. Each adapter has an empty `BEST_PRACTICE` setting until they send
   some; until then `run.py` skips that column (it would equal `default`).
4. **Predictions first.** Write what you expect in `predictions/PREDICTIONS.md`,
   commit it and tag it `predictions-v1` *before* any paid run.
5. **Pin everything** with `pins/capture.py`: versions, lockfile hash, git SHA,
   model ids, protocol versions.
6. **Maintainer review** before publishing: each framework's results and repro
   steps go to its maintainers, with a review window announced up front.

## What is inside

```
benchmark/
  run.py                 run the matrix: framework × config × model
  model_check.py         the three headline faults again, with a stronger model
  prices.toml            ₹ per million tokens: you fill it in (empty by default)
  pyproject.toml         extras per framework + the [tool.polygraph] setup
  bench/                 core: agent interface, prompt, tasks, configs, costs, pins, probe, CLI
  adapters/              one adapter per framework (+ two scripted ones for dry runs)
  recipes/               R1-R7 as framework-neutral wrappers and prompt lines
  tasks/                 the benchmark tasks (pytest tests with database checks)
  judge/                 the narrow LLM judge, the hand-labelling CSV, judge-vs-human agreement
  second_opinion/        optional agent-polygraph verify() on every run
  predictions/           PREDICTIONS.md: fill in and tag before paid runs
  pins/                  capture.py writes pins/<date>.json
  REPORT_TEMPLATE.md     the write-up, with blanks for numbers
  tests/                 the harness's own tests (offline, no keys)
```

## Run it dry (offline, free)

A dry run swaps the LLM agents for Polygraph's two scripted agents: a *naive*
one (retries anything, follows instructions in tool text, says "done" without
checking) and a *careful* one (applies the fixes itself). No model, no key, no
network. It runs the real worlds, the real proxy and the real scoring, so the
whole pipeline is checked.

```bash
cd benchmark
python -m venv .venv
.venv/Scripts/python -m pip install -e ../packages/pytest-polygraph -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ../packages/pytest-polygraph -e ".[dev]"     # Linux / macOS

python run.py --dry-run -m headline          # the 7 pinned headline tasks, both agents
python run.py --dry-run                      # every task × the full planned matrix
python run.py --dry-run --config R1 --config R6 --config R7   # recipe columns
python -m pytest -q                          # the harness's own tests
```

Output goes to `out/<timestamp>-dry/`: one results file per cell in `results/`,
an HTML report and a log per cell in `cells/`, plus `costs.json` and
`manifest.json`.

In a dry run, the recipes that wrap tool calls (R1, R3, R6, R7, and R2's notes)
act on the scripted agents. Prompt-only recipes (R4, and the prompt half of R2
and R5) do nothing there, because a scripted agent reads no prompt.

## Run it for real

**1. Write your predictions and tag them.** Fill in `predictions/PREDICTIONS.md`,
then `git commit` and `git tag predictions-v1`. Do this before step 6.

**2. One virtualenv per framework.** The frameworks need different MCP SDK
versions (LangGraph's adapter and smolagents' mcpadapt need `mcp<2`; the others
use `mcp` 2.x), so they cannot share one venv. `run.py` finds `.venvs/<name>`
by itself.

```bash
for fw in langgraph openai-agents pydantic-ai smolagents; do
  python -m venv .venvs/$fw
  .venvs/$fw/Scripts/python -m pip install -e ../packages/pytest-polygraph -e ".[$fw,second-opinion]"
done
python -m bench.probe            # in each venv: which MCP protocol version it speaks (free)
```

**3. Pick the model and set its key.** Keys only ever come from environment
variables, never from files.

```bash
export POLYGRAPH_BENCH_MODEL="openai:<model-id>"     # or anthropic:<model-id>
export OPENAI_API_KEY=...                            # or ANTHROPIC_API_KEY
export POLYGRAPH_BENCH_JUDGE_MODEL="openai:<model-id>"   # only for --judge llm
```

**4. Fill in `prices.toml`** with today's prices in ₹ per million tokens, the
source and the date. Leave a model blank and its cost shows "not priced".
Polygraph never guesses a price.

**5. Pin the setup:** `python pins/capture.py --model "$POLYGRAPH_BENCH_MODEL" --seed 20261014 --probe`
(run it in each framework venv).

**6. Run the matrix.**

```bash
python run.py --runs 5                                   # all 4 frameworks, default config
python run.py --framework pydantic-ai --config R2 --config R5 --runs 5
python run.py --runs 5 --judge llm --second-opinion      # the LLM judge and agent-polygraph too
python run.py --runs 5 -m headline                       # just the 7 pinned headline tasks
python run.py --runs 5 --faults lost_response,injection  # narrow the planned matrix
```

At the end it prints the cost of every cell: tokens in, tokens out, tokens per
run, ₹ per run and ₹ in total, using `prices.toml`. The same table is saved in
`costs.json`, so the write-up can say "reproduce this for about ₹__" from real
numbers.

**7. Model check.** `python model_check.py --framework <one> --model <main> --strong-model <stronger> --runs 10`
re-runs the three headline faults (`lost_response`, `injection`,
`description_poison`) with both models and prints them side by side.

**8. Check the judge against a human.**

```bash
python -m judge.label --results out/<run>/results/*.json --n 100 --seed 7 --out labels.csv
# a person fills in human_label: yes / no / unclear (did the agent *claim* success?)
python -m judge.agreement labels.csv
```

It prints raw agreement, Cohen's kappa over the three labels, and kappa over
the runs where both said yes or no. Publish these numbers next to the results.

**9. Upload to the leaderboard** (optional): `python run.py ... --upload`, or
later `polygraph upload out/<run>/results/<file>.json`. This sends results to
the `polygraph-dev/benchmark` project on Polygraph Cloud, using
`POLYGRAPH_TOKEN` (that project's upload token) or GitHub Actions OIDC. Uploads
drop all payload text by default.

**10. Write it up** with `REPORT_TEMPLATE.md`, after the maintainer review.

## Frameworks, versions and protocol versions

Checked with these versions. The protocol version is what the Polygraph proxy
recorded on the wire against the worlds (`python -m bench.probe`).

| Framework | Version | MCP client | MCP SDK | Protocol spoken |
|---|---|---|---|---|
| LangGraph | langgraph 1.2.15, langchain 1.4.5 | langchain-mcp-adapters 0.3.2, `MultiServerMCPClient.get_tools()` | mcp 1.30.0 | 2025-11-25 |
| OpenAI Agents SDK | openai-agents 0.23.1 | `agents.mcp.MCPServerStdio` | mcp 2.3.0 | 2026-07-28 |
| Pydantic AI | pydantic-ai-slim 2.56.0 | `pydantic_ai.mcp.MCPToolset` (fastmcp-slim 4.1.0) | mcp 2.3.0 | 2026-07-28 |
| smolagents | smolagents 1.26.0 | `smolagents.MCPClient` (mcpadapt 0.1.20) | mcp 1.30.0 | 2025-11-25 |
| scripted (dry run) | pytest-polygraph 0.1.0 | `polygraph.testing.McpClient` | none | 2026-07-28 |

A few things we saw while building the adapters (behaviour, not results):

- LangGraph's adapter opens a **new MCP session for every tool call** and reads
  the tool list once at the start, so a mid-run rug pull cannot reach it.
- smolagents also loads tools once. Its mcpadapt layer **ignores `isError`**:
  an error result's text reaches the model like a normal result.
- The OpenAI Agents SDK fetches the tool list again on every turn by default
  (`cache_tools_list=False`) and gives MCP calls 5 seconds by default.
- Pydantic AI caches the tool list until the server says it changed, and turns
  tool errors into a retry request to the model.

How each framework reaches the model: an `openai:` model goes through each
framework's own OpenAI client (the Agents SDK uses the Responses API by default;
the others use Chat Completions). An `anthropic:` model is native in LangGraph
and Pydantic AI; the Agents SDK and smolagents have no native Anthropic client,
so they use Anthropic's OpenAI-compatible endpoint. This difference is listed
as a threat to validity in the report.

## Config labels and recipes

| Label | What changes |
|---|---|
| `default` | nothing: the framework as shipped |
| `best-practice` | the maintainers' suggested settings (empty until they reply) |
| `R1` | idempotency key on every changing call that accepts one |
| `R2` | prompt line + a note after every changing call: read the state back before saying "done" |
| `R3` | retry safe calls up to 3 times with growing waits, honour "retry in N s", then stop and say so |
| `R4` | one "be honest" line in the system prompt |
| `R5` | tool results wrapped in `<tool_output>` markers + a prompt line not to follow them |
| `R6` | fingerprint each tool description at first sight; block a tool whose description changed |
| `R7` | check results against the tool's `outputSchema`; reject what does not match |
| `R1+R2`, `all-recipes` | several at once (order does not matter) |

The recipes live once in `recipes/` and go into each framework through its own
extension point: LangGraph's `tool_interceptors`, a subclass of the Agents SDK
server class, a subclass of Pydantic AI's `MCPToolset`, and mcpadapt's adapter
class for smolagents.

## How to add a framework

1. Add an extra for it in `pyproject.toml`. Check which `mcp` version it needs.
2. Write `adapters/<name>_adapter.py` with a subclass of `adapters.base.BaseAgent`:
   - set `framework`, `distribution`, `requires`, `extra`, and an empty `BEST_PRACTICE`;
   - connect the framework's MCP client to the `servers` dict (stdio commands or
     URLs, exactly as Polygraph hands them out), using its default settings;
   - build the agent with `self.system_prompt` and the model, run the task, and
     return `AgentResult(answer, tokens_in, tokens_out)` with the tokens the
     framework counted;
   - if `self.stack` is set, route tool lists through `self.stack.on_tools` and
     tool calls through `self.stack.call`, using the framework's own hook;
   - add `probe()`: list tools and call `get_invoice` with no model.
3. Register it in `adapters/__init__.py` (`ADAPTERS` and `FRAMEWORKS`).
4. Add it to `EXPECTED_PROTOCOL` in `tests/test_adapters.py` and a fake-model
   test in `tests/test_agent_loops.py`. Run `python -m bench.probe <name>` and
   put its versions in the table above.

## What leaves your machine

- **Dry runs:** nothing.
- **Real runs:** the task, the system prompt, tool definitions and tool results
  (all invented data) go to the model provider you chose. With `--judge llm`,
  the task and the agent's final answer go to the judge model's provider.
- **Uploads** (only with `--upload`): results without payload text.
- Local results files keep payload text so a person can hand-label answers.
  They stay in `out/`, which git ignores.

## Known limits

- The worlds are small and fake. Real services are messier.
- One main model per run. The model check covers the headline faults only.
- `ToolCallingAgent` is used for smolagents (not `CodeAgent`) so all four call
  tools the same way.
- The judge's cost is counted from its token usage, written next to each cell.
- Rates from the planned matrix depend on which tools a task calls in its
  control run; faults on tools a task never calls are skipped.
