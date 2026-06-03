export type StatusKind = "idle" | "scanning" | "queued" | "cancelled" | "error";

export interface UsageSummary {
  totalTokens: number;
  totalTokensLabel: string;
  inputTokens: number;
  inputLabel: string;
  cachedTokens: number;
  cachedLabel: string;
  outputTokens: number;
  outputLabel: string;
  reasoningTokens: number;
  reasoningLabel: string;
  requests: number;
  requestsLabel: string;
  failures: number;
  successRateLabel: string;
  cacheHitLabel: string;
  peakLabel: string;
  peakTime: string;
  peakTpmLabel: string;
}

export interface CostSummary {
  total: number;
  average: number;
  rangeTokensLabel: string;
  unpricedTokens: number;
  parts: Array<{ key: string; name: string; value: number; percent: number }>;
}

export interface RankingRow {
  rank: number;
  name: string;
  model: string;
  tokens: number;
  tokensLabel: string;
  requests: number;
  tokenPercent: number;
  requestPercent: number;
  status: "ok" | "warn";
}

export interface ModelRow {
  name: string;
  tokens: number;
  tokensLabel: string;
  requests: number;
  input: number;
  cached: number;
  output: number;
  reasoning: number;
  latencyLabel: string;
  cost: number;
  percent: number;
}

export interface RiskRow {
  name: string;
  value: number;
  label: string;
  percentLabel: string;
  note: string;
  tone: string;
}

export interface CodexView {
  key: string;
  label: string;
  range: { start: number; end: number };
  axisGranularity?: "minute" | "hour" | "day" | "month" | "year";
  summary: UsageSummary;
  cost: CostSummary;
  trend: Array<[number, number, number, number, number, number, number, number]>;
  distribution: Array<[number, number, number, number]>;
  sessions: RankingRow[];
  models: ModelRow[];
  costModels: Array<{ rank: number; name: string; cost: number; percent: number }>;
  risk: RiskRow[];
}

export interface CodexPayload {
  schemaVersion: number;
  source: "codex";
  generatedAt: string;
  root: string;
  pricingRules: Array<{ label: string; patterns: string[]; input: number; cached: number; output: number }>;
  records: Array<[number, string, string, number, number, number, number, number]>;
  ttfbRecords: Array<[number, string, string, number]>;
  failureRecords: Array<[number, string, string]>;
  views: Record<string, CodexView>;
  summary: UsageSummary;
  cost: CostSummary;
  sessions: RankingRow[];
  models: ModelRow[];
  risk: RiskRow[];
  coverage: Array<{ metric: string; source: string; status: string }>;
}

export interface HostMessage {
  type: string;
  [key: string]: unknown;
}
