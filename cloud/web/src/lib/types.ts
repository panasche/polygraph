// Mirrors docs/CONTRACTS.md §4 and packages/pytest-polygraph/src/polygraph/results.py.
// Keep in sync with both.

export type Outcome =
  | "recovered"
  | "honest_fail"
  | "silent_lie"
  | "double_action"
  | "hijacked"
  | "loop"
  | "crash";

export type FaultName =
  | "timeout"
  | "slow"
  | "error"
  | "rate_limit"
  | "auth_expired"
  | "lost_response"
  | "duplicate_delivery"
  | "partial"
  | "malformed"
  | "schema_drift"
  | "stale"
  | "injection"
  | "description_poison";

export type LabelSource = "override" | "server" | "guessed" | "default";

export interface Rate {
  numerator: number;
  denominator: number;
  value: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export interface Payload {
  sha256: string;
  size: number;
  text: string | null;
}

export interface HintValue {
  value: boolean;
  source: LabelSource;
}

export interface ToolInfo {
  server: string;
  name: string;
  hints: { read_only: HintValue; destructive: HintValue; idempotent: HintValue; open_world: HintValue };
  description_sha256: string | null;
  has_output_schema: boolean;
}

export interface FaultSpec {
  name: FaultName;
  server: string;
  tool: string;
  on_call: number;
  params: Record<string, unknown>;
}

export interface CoveragePair {
  server: string;
  tool: string;
  fault: FaultName;
  priority: "top" | "normal";
  runs_required: number;
  runs_done: number;
}

export interface Coverage {
  applicable: number;
  covered: number;
  pairs: CoveragePair[];
}

export interface CoverageCount {
  applicable: number;
  covered: number;
}

export interface Happened {
  forwarded: number;
  server_result: Payload | null;
  server_is_error: boolean;
  note: string | null;
}

export interface LedgerEntry {
  seq: number;
  ts: string;
  server: string;
  method: string;
  tool: string | null;
  request_id: string | number | null;
  protocol_version: string | null;
  fault: FaultName | null;
  asked: Payload;
  happened: Happened;
  told: Payload | null;
  told_is_error: boolean;
  latency_ms: number;
}

export interface StateCheck {
  name: string;
  passed: boolean;
  detail: string | null;
}

export interface JudgeVerdict {
  claimed_success: boolean | null;
  method: "rules" | "llm" | "manual";
  model: string | null;
  rationale: string | null;
}

export interface SecondOpinion {
  tool: string;
  flagged_lie: boolean;
  agrees_with_polygraph: boolean;
  detail: string | null;
}

export interface Run {
  id: string;
  test_id: string;
  task: string | null;
  fault: FaultSpec | null;
  seed: number;
  repeat: number;
  started_at: string;
  duration_ms: number;
  outcome: Outcome;
  reasons: string[];
  judge: JudgeVerdict | null;
  second_opinion: SecondOpinion | null;
  final_answer: Payload | null;
  state_checks: StateCheck[];
  ledger: LedgerEntry[];
  tokens: { input: number; output: number } | null;
  error: string | null;
  protocol_version: string | null;
}

export interface RunDetail extends Run {
  report_id: string;
  content_included: boolean;
}

export interface ClaimCheck {
  server: string;
  tool: string;
  claim: "idempotent" | "read_only";
  held: boolean;
  evidence: string;
  run_id: string | null;
}

export interface PassK {
  k: number;
  groups: number;
  passed: number;
}

export interface Summary {
  runs: number;
  faulted_runs: number;
  outcomes: Record<Outcome, number>;
  success: Rate;
  silent_lie: Rate;
  double_action: Rate;
  hijack: Rate;
  loop: Rate;
  crash: Rate;
  pass_k: PassK | null;
  cost_multiplier: number | null;
  coverage_ratio: number | null;
}

export interface Environment {
  polygraph_version: string;
  python_version: string;
  platform: string;
  ci: string | null;
  git: {
    sha: string | null;
    branch: string | null;
    ref: string | null;
    pr_number: number | null;
    base_branch: string | null;
    repository: string | null;
  };
  framework: string | null;
  framework_version: string | null;
  config_label: string | null;
  model: string | null;
  protocol_versions: string[];
  seed: number;
  extra: Record<string, string>;
}

export interface User {
  id: string;
  login: string;
  avatar_url: string | null;
  consented_privacy_version: string | null;
  created_at: string;
}

export type Role = "admin" | "member" | "viewer" | null;

export type ReportStatus = "queued" | "processing" | "ready" | "rejected";

export interface ReportLite {
  id: string;
  project: string;
  status: ReportStatus;
  rejection_reason: string | null;
  source: "oidc" | "token";
  branch: string | null;
  sha: string | null;
  pr_number: number | null;
  created_at: string;
  framework: string | null;
  model: string | null;
  config_label: string | null;
  runs: number;
  faulted_runs: number;
  success: Rate;
  silent_lie: Rate;
  double_action: Rate;
  hijack: Rate;
  coverage: CoverageCount;
}

export interface HeatCell {
  server: string;
  tool: string;
  fault: FaultName;
  runs: number;
  outcomes: Partial<Record<Outcome, number>>;
  bad: number;
}

export interface RunLite {
  id: string;
  test_id: string;
  task: string | null;
  fault: FaultSpec | null;
  repeat: number;
  outcome: Outcome;
  reasons: string[];
  duration_ms: number;
}

export interface ReportDetail extends ReportLite {
  environment: Environment;
  summary: Summary;
  coverage_detail: Coverage;
  tools: ToolInfo[];
  server_claims: ClaimCheck[];
  content_included: boolean;
  heatmap: HeatCell[];
  run_list: RunLite[];
}

export interface Project {
  id: string;
  owner: string;
  repo: string;
  full_name: string;
  is_private: boolean;
  public_results: boolean;
  allow_text_upload: boolean;
  default_branch: string;
  created_at: string;
  last_report_at: string | null;
  role: Role;
  latest: ReportLite | null;
}

export interface Branch {
  name: string;
  last_report_at: string | null;
  reports: number;
}

export interface TrendPoint {
  report_id: string;
  created_at: string;
  sha: string | null;
  success: Rate;
  silent_lie: Rate;
  double_action: Rate;
  hijack: Rate;
  coverage: CoverageCount;
}

export interface Trends {
  branch: string;
  points: TrendPoint[];
}

export interface ComparePair {
  server: string;
  tool: string;
  fault: FaultName;
  /** null when the pair was only tested in the other report. */
  base: { runs: number; bad: number } | null;
  head: { runs: number; bad: number } | null;
}

export type CompareMetric = "silent_lie" | "double_action" | "hijack" | "success";

export interface Compare {
  base: ReportLite;
  head: ReportLite;
  metrics: Record<CompareMetric, { base: Rate; head: Rate }>;
  coverage: { base: CoverageCount; head: CoverageCount };
  pairs: ComparePair[];
}

export interface UploadToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  created_by: string;
}

export interface LeaderboardEntry {
  framework: string;
  framework_version: string | null;
  config_label: string | null;
  model: string | null;
  report_id: string;
  runs: number;
  success: Rate;
  silent_lie: Rate;
  double_action: Rate;
  hijack: Rate;
  pass_k: PassK | null;
}

export interface Leaderboard {
  project: string;
  updated_at: string | null;
  entries: LeaderboardEntry[];
}

export interface Paged<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
