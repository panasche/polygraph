import type { FaultName } from "./types";

export const RECIPES: { id: string; title: string; plain: string; fixes: (FaultName | "all")[]; how: string }[] = [
  {
    id: "R1",
    title: "Idempotency key",
    plain: "Give every “do something” call a unique ID, so the server ignores repeats.",
    fixes: ["lost_response", "duplicate_delivery"],
    how: "Generate the key once per intent, before the first attempt, and reuse it on every retry.",
  },
  {
    id: "R2",
    title: "Check before saying “done”",
    plain: "After an action, read the state back. Only then claim success.",
    fixes: ["lost_response", "error", "timeout"],
    how: "Pair each changing tool with a read tool, and make the read part of the plan, not an afterthought.",
  },
  {
    id: "R3",
    title: "Retry cap and backoff",
    plain: "Retry a few times, wait longer each time, respect “retry after”, then give up honestly.",
    fixes: ["rate_limit", "timeout", "slow"],
    how: "Cap attempts at three, use jittered exponential backoff, and report the last error when you stop.",
  },
  {
    id: "R4",
    title: "A “be honest” prompt line",
    plain: "One line in the system prompt. Tests whether prompting alone helps, which is interesting either way.",
    fixes: ["all"],
    how: "“If a tool call failed or you can't confirm the result, say so plainly. Never report success you haven't verified.”",
  },
  {
    id: "R5",
    title: "Tool output is data, not orders",
    plain: "Wrap tool results in clear markers and tell the model never to follow instructions inside them.",
    fixes: ["injection"],
    how: "Delimit every result, and restate in the system prompt that only the user gives instructions.",
  },
  {
    id: "R6",
    title: "Pin tool descriptions",
    plain: "Save a fingerprint of each tool's description at start, and refuse a tool whose description changed.",
    fixes: ["description_poison"],
    how: "Hash name, description and input schema on first list. On a mismatch, stop and ask a human.",
  },
  {
    id: "R7",
    title: "Validate the shape",
    plain: "Check results against the tool's outputSchema. The MCP spec says clients should.",
    fixes: ["schema_drift", "malformed"],
    how: "Reject results that don't match, and treat the call as failed instead of guessing at fields.",
  },
];

export const FAQ: { q: string; a: string; id?: string }[] = [
  {
    q: "What gets uploaded?",
    a: "Only if you turn uploads on: numbers, outcome buckets, tool and fault names, timings, and hashes of payloads. Not the text itself. Text upload is a separate per-project opt-in, your mask runs on your machine first, and the server rejects anything that still looks like personal data.",
  },
  {
    q: "Do you see my API keys?",
    a: "No. The dashboard never runs your agent and never calls a model. From GitHub Actions it needs no secret at all: GitHub signs a short-lived token that proves which repo the upload came from.",
  },
  {
    q: "Can I delete my data?",
    a: "Yes. Delete a report, a project or your whole account at any time, and export everything as JSON first if you like. Deletes remove the stored files too.",
  },
  {
    q: "Do I need the dashboard at all?",
    a: "No. The pytest plugin, the HTML report and the ledger view all work offline. The dashboard adds trends over time, PR comparisons and a live badge.",
  },
  {
    id: "how-is-this-different",
    q: "How is this different from agent-polygraph or Langfuse?",
    a: "agent-polygraph reads a finished transcript and flags claims the transcript contradicts. Polygraph causes the failure and checks the server's real state, which catches lies a transcript can't show. Langfuse and similar tools show production traces; Polygraph shows test results. It can send its spans to them over OpenTelemetry.",
  },
  {
    q: "Which agent frameworks work?",
    a: "Anything that talks to MCP servers. Polygraph sits between the agent and the server as a proxy, over stdio or Streamable HTTP, and speaks both the older and the current (2026-07-28) protocol versions.",
  },
];

export const LIMITS: { label: string; value: string }[] = [
  { label: "Upload size", value: "10 MB compressed" },
  { label: "Runs per upload", value: "5,000" },
  { label: "Uploads per project", value: "60 per hour" },
  { label: "Full run ledgers kept", value: "90 days" },
  { label: "Summary numbers kept", value: "2 years" },
];
