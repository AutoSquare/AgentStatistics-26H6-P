import type {
  AgentDataStatus,
  AgentPayload,
  AgentSource,
  AgentView,
  CostSummary,
  ModelRow,
  RankingRow,
  StatusKind,
  TotalAgentPayload,
  UsageSummary
} from "./types";

const VIEW_KEYS = ["today", "24h", "7", "30", "history"] as const;
type DataAgentSource = Exclude<AgentSource, "total">;
const SOURCE_ORDER: DataAgentSource[] = ["codex", "cursor", "antigravity"];
const SOURCE_LABELS: Record<DataAgentSource, string> = {
  codex: "Codex",
  cursor: "Cursor",
  antigravity: "Antigravity"
};

type TrendRow = AgentView["trend"][number];
type DistributionRow = AgentView["distribution"][number];
type SummaryWithPeak = UsageSummary & { peakTokens?: number };

export function mergeAgentPayloads(
  codex: AgentPayload | null,
  cursor: AgentPayload | null,
  antigravity: AgentPayload | null
): TotalAgentPayload | null {
  const sources = SOURCE_ORDER.map((source) => ({
    source,
    payload: source === "codex" ? codex : source === "cursor" ? cursor : antigravity
  })).filter((item): item is { source: DataAgentSource; payload: AgentPayload } => item.payload !== null);

  if (sources.length === 0) return null;

  const pricingRules = mergePricingRules(sources.map((item) => item.payload));
  const records = mergeRecords(sources);
  const ttfbRecords = mergeTtfbRecords(sources);
  const failureRecords = mergeFailureRecords(sources);
  const views = mergeViewsMap(sources);
  const history = views.history;
  const generatedAt = latestGeneratedAt(sources.map((item) => item.payload.generatedAt));

  return {
    schemaVersion: 2,
    source: "total",
    generatedAt,
    root: "cross-agent",
    dataStatus: resolveDataStatus(sources, records.length),
    pricingRules,
    records,
    ttfbRecords,
    failureRecords,
    views,
    summary: history.summary,
    cost: history.cost,
    sessions: history.sessions,
    models: history.models,
    risk: [],
    coverage: mergeCoverage(sources)
  };
}

export function buildTotalStatusKind(statuses: Record<DataAgentSource, StatusKind>): StatusKind {
  const values = SOURCE_ORDER.map((source) => statuses[source]);
  if (values.some((status) => status === "scanning")) return "scanning";
  if (values.some((status) => status === "error")) return "error";
  if (values.some((status) => status === "queued")) return "queued";
  if (values.some((status) => status === "cancelled")) return "cancelled";
  return "idle";
}

export function buildTotalStatusMessage(statuses: Record<DataAgentSource, StatusKind>, messages: Record<DataAgentSource, string>): string {
  return SOURCE_ORDER.map((source) => {
    const label = SOURCE_LABELS[source];
    const status = statuses[source];
    if (status === "scanning") return `${label} 扫描中`;
    if (status === "error") return `${label} 失败`;
    const text = messages[source] || "等待数据";
    if (text.includes("已同步") || text.includes("已刷新")) return `${label} 已同步`;
    if (text.includes("暂无")) return `${label} 无数据`;
    return `${label}：${text}`;
  }).join(" · ");
}

function mergeRecords(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>) {
  const merged: AgentPayload["records"] = [];
  for (const item of sources) {
    for (const row of item.payload.records) {
      merged.push([row[0], `${item.source}:${row[1]}`, row[2], row[3], row[4], row[5], row[6], row[7], row[8]]);
    }
  }
  merged.sort((left, right) => left[0] - right[0]);
  return merged;
}

function mergeTtfbRecords(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>) {
  const merged: AgentPayload["ttfbRecords"] = [];
  for (const item of sources) {
    for (const row of item.payload.ttfbRecords) {
      merged.push([row[0], `${item.source}:${row[1]}`, row[2], row[3]]);
    }
  }
  return merged;
}

function mergeFailureRecords(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>) {
  const merged: AgentPayload["failureRecords"] = [];
  for (const item of sources) {
    for (const row of item.payload.failureRecords) {
      merged.push([row[0], `${item.source}:${row[1]}`, row[2]]);
    }
  }
  return merged;
}

function mergePricingRules(payloads: AgentPayload[]) {
  const seen = new Set<string>();
  const merged: AgentPayload["pricingRules"] = [];
  for (const payload of payloads) {
    for (const rule of payload.pricingRules) {
      if (seen.has(rule.label)) continue;
      seen.add(rule.label);
      merged.push(rule);
    }
  }
  return merged;
}

function mergeViewsMap(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>) {
  const views = {} as Record<string, AgentView>;
  for (const key of VIEW_KEYS) {
    const parts = sources
      .map((item) => item.payload.views[key])
      .filter((view): view is AgentView => Boolean(view));
    if (parts.length === 0) continue;
    views[key] = parts.reduce((left, right) => mergeView(left, right));
  }
  return views;
}

function mergeView(left: AgentView, right: AgentView): AgentView {
  return {
    key: left.key,
    label: left.label || right.label,
    range: {
      start: Math.min(left.range.start, right.range.start),
      end: Math.max(left.range.end, right.range.end)
    },
    axisGranularity: left.axisGranularity ?? right.axisGranularity,
    summary: mergeSummary(left.summary as SummaryWithPeak, right.summary as SummaryWithPeak),
    cost: mergeCost(left.cost, right.cost, left.summary.requests + right.summary.requests),
    trend: mergeTrendRows(left.trend, right.trend),
    distribution: mergeDistributionRows(left.distribution, right.distribution),
    sessions: mergeSessionRows(left.sessions, right.sessions),
    models: mergeModelRows(left.models, right.models),
    costModels: mergeCostModelRows(left.costModels, right.costModels),
    risk: []
  };
}

function mergeSummary(left: SummaryWithPeak, right: SummaryWithPeak): UsageSummary {
  const leftPeak = left.peakTokens ?? parsePeakTokens(left.peakLabel);
  const rightPeak = right.peakTokens ?? parsePeakTokens(right.peakLabel);
  const peakWinner = rightPeak > leftPeak ? right : left;
  const inputTokens = left.inputTokens + right.inputTokens;
  const cachedTokens = left.cachedTokens + right.cachedTokens;
  const outputTokens = left.outputTokens + right.outputTokens;
  const reasoningTokens = left.reasoningTokens + right.reasoningTokens;
  const totalTokens = left.totalTokens + right.totalTokens;
  const requests = left.requests + right.requests;
  const failures = left.failures + right.failures;
  const cacheHit = inputTokens > 0 ? (cachedTokens / inputTokens) * 100 : 0;
  const successRate = requests > 0 ? ((requests - failures) / requests) * 100 : 100;
  const peakTokens = Math.max(leftPeak, rightPeak);
  return {
    totalTokens,
    totalTokensLabel: fmtInt(totalTokens),
    inputTokens,
    inputLabel: fmtInt(inputTokens),
    cachedTokens,
    cachedLabel: fmtInt(cachedTokens),
    outputTokens,
    outputLabel: fmtInt(outputTokens),
    reasoningTokens,
    reasoningLabel: fmtInt(reasoningTokens),
    requests,
    requestsLabel: comma(requests),
    failures,
    successRateLabel: `${successRate.toFixed(1)}%`,
    cacheHitLabel: `${cacheHit.toFixed(1)}%`,
    peakLabel: peakWinner.peakLabel,
    peakTime: peakWinner.peakTime,
    peakTpmLabel: peakTokens > 0 ? `${fmtInt(peakTokens)} TPM` : peakWinner.peakTpmLabel
  };
}

function mergeCost(left: CostSummary, right: CostSummary, requests: number): CostSummary {
  const total = left.total + right.total;
  const partMap = new Map<string, { key: string; name: string; value: number }>();
  for (const part of [...left.parts, ...right.parts]) {
    const existing = partMap.get(part.key) ?? { key: part.key, name: part.name, value: 0 };
    existing.value += part.value;
    partMap.set(part.key, existing);
  }
  const parts = Array.from(partMap.values())
    .map((part) => ({
      ...part,
      percent: total > 0 ? Math.round((part.value / total) * 100) : 0
    }))
    .sort((a, b) => b.value - a.value);
  return {
    total,
    average: requests > 0 ? total / requests : 0,
    rangeTokensLabel: fmtInt(parseTokenCount(left.rangeTokensLabel) + parseTokenCount(right.rangeTokensLabel)),
    unpricedTokens: left.unpricedTokens + right.unpricedTokens,
    parts
  };
}

function mergeTrendRows(left: TrendRow[], right: TrendRow[]): TrendRow[] {
  const bucketMap = new Map<number, TrendRow>();
  for (const row of [...left, ...right]) {
    const existing = bucketMap.get(row[0]) ?? [row[0], 0, 0, 0, 0, 0, 0, 0];
    bucketMap.set(row[0], [
      row[0],
      existing[1] + row[1],
      existing[2] + row[2],
      existing[3] + row[3],
      existing[4] + row[4],
      existing[5] + row[5],
      existing[6] + row[6],
      Number((existing[7] + row[7]).toFixed(6))
    ]);
  }
  return Array.from(bucketMap.values()).sort((a, b) => a[0] - b[0]);
}

function mergeDistributionRows(left: DistributionRow[], right: DistributionRow[]): DistributionRow[] {
  const bucketMap = new Map<number, DistributionRow>();
  for (const row of [...left, ...right]) {
    const existing = bucketMap.get(row[0]) ?? [row[0], 0, 0, 0];
    bucketMap.set(row[0], [row[0], existing[1] + row[1], existing[2] + row[2], Number((existing[3] + row[3]).toFixed(6))]);
  }
  return Array.from(bucketMap.values()).sort((a, b) => a[0] - b[0]);
}

function mergeSessionRows(left: RankingRow[], right: RankingRow[]): RankingRow[] {
  const map = new Map<string, RankingRow & { peakTokens?: number }>();
  for (const row of [...left, ...right]) {
    const key = `${row.name}::${row.model}`;
    const existing = map.get(key);
    if (!existing) {
      map.set(key, { ...row });
      continue;
    }
    existing.tokens += row.tokens;
    existing.requests += row.requests;
    existing.status = existing.status === "warn" || row.status === "warn" ? "warn" : "ok";
    map.set(key, existing);
  }
  const rows = Array.from(map.values()).sort((a, b) => b.tokens - a.tokens).slice(0, 20);
  const maxTokens = Math.max(1, ...rows.map((row) => row.tokens));
  const maxRequests = Math.max(1, ...rows.map((row) => row.requests));
  return rows.map((row, index) => ({
    rank: index + 1,
    name: row.name,
    model: row.model,
    tokens: row.tokens,
    tokensLabel: fmtInt(row.tokens),
    requests: row.requests,
    tokenPercent: Math.round((row.tokens / maxTokens) * 100),
    requestPercent: Math.round((row.requests / maxRequests) * 100),
    status: row.status
  }));
}

function mergeModelRows(left: ModelRow[], right: ModelRow[]): ModelRow[] {
  const map = new Map<string, ModelRow & { latencyTotal: number; latencyCount: number }>();
  for (const row of [...left, ...right]) {
    const existing = map.get(row.name);
    const latency = parseLatencySeconds(row.latencyLabel);
    if (!existing) {
      map.set(row.name, {
        ...row,
        latencyTotal: latency.value * latency.count,
        latencyCount: latency.count
      });
      continue;
    }
    existing.tokens += row.tokens;
    existing.requests += row.requests;
    existing.input += row.input;
    existing.cached += row.cached;
    existing.output += row.output;
    existing.reasoning += row.reasoning;
    existing.cost += row.cost;
    existing.latencyTotal += latency.value * latency.count;
    existing.latencyCount += latency.count;
    map.set(row.name, existing);
  }
  const rows = Array.from(map.values()).sort((a, b) => b.tokens - a.tokens).slice(0, 12);
  const maxTokens = Math.max(1, ...rows.map((row) => row.tokens));
  return rows.map((row) => {
    const latency = row.latencyCount > 0 ? row.latencyTotal / row.latencyCount : 0;
    return {
      name: row.name,
      tokens: row.tokens,
      tokensLabel: fmtInt(row.tokens),
      requests: row.requests,
      input: row.input,
      cached: row.cached,
      output: row.output,
      reasoning: row.reasoning,
      latencyLabel: row.latencyCount > 0 ? `${latency.toFixed(2)}s` : "--",
      cost: row.cost,
      percent: Math.round((row.tokens / maxTokens) * 100)
    };
  });
}

function mergeCostModelRows(left: AgentView["costModels"], right: AgentView["costModels"]) {
  const map = new Map<string, { name: string; cost: number }>();
  for (const row of [...left, ...right]) {
    const existing = map.get(row.name) ?? { name: row.name, cost: 0 };
    existing.cost += row.cost;
    map.set(row.name, existing);
  }
  const rows = Array.from(map.values()).sort((a, b) => b.cost - a.cost).slice(0, 4);
  const maxCost = Math.max(1, ...rows.map((row) => row.cost));
  return rows.map((row, index) => ({
    rank: index + 1,
    name: row.name,
    cost: row.cost,
    percent: Math.round((row.cost / maxCost) * 100)
  }));
}

function mergeCoverage(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>) {
  return sources.flatMap((item) =>
    (item.payload.coverage ?? []).map((entry) => ({
      metric: entry.metric,
      source: item.source,
      status: entry.status
    }))
  );
}

function resolveDataStatus(sources: Array<{ source: DataAgentSource; payload: AgentPayload }>, recordCount: number): AgentDataStatus {
  if (recordCount > 0) return "ok";
  const statuses = sources.map((item) => item.payload.dataStatus).filter(Boolean) as AgentDataStatus[];
  if (statuses.some((status) => status === "sync_failed")) return "sync_failed";
  if (statuses.some((status) => status === "parse_empty")) return "parse_empty";
  return "empty";
}

function latestGeneratedAt(values: string[]) {
  let latest = values[0] ?? "";
  let latestTime = parseGeneratedAt(latest);
  for (const value of values.slice(1)) {
    const time = parseGeneratedAt(value);
    if (time >= latestTime) {
      latest = value;
      latestTime = time;
    }
  }
  return latest ? `${latest}（合并快照）` : "合并快照";
}

function parseGeneratedAt(value: string) {
  const parsed = Date.parse(value.replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : 0;
}

function parsePeakTokens(label: string) {
  const normalized = label.replace(/,/g, "");
  if (normalized.endsWith("亿")) return Number.parseFloat(normalized) * 100_000_000;
  if (normalized.endsWith("万")) return Number.parseFloat(normalized) * 10_000;
  const plain = Number.parseInt(normalized, 10);
  return Number.isFinite(plain) ? plain : 0;
}

function parseTokenCount(label: string) {
  return parsePeakTokens(label);
}

function parseLatencySeconds(label: string) {
  if (!label || label === "--") return { value: 0, count: 0 };
  const match = label.match(/([\d.]+)s/);
  if (!match) return { value: 0, count: 0 };
  return { value: Number.parseFloat(match[1]), count: 1 };
}

function formatSignificant(value: number, digits = 4) {
  if (value === 0) return "0.0";
  const integerDigits = String(Math.trunc(Math.abs(value))).length;
  const decimals = Math.max(1, digits - integerDigits);
  return value.toFixed(decimals);
}

function fmtInt(value: number) {
  const amount = Math.trunc(value || 0);
  const abs = Math.abs(amount);
  if (abs >= 100_000_000) return `${formatSignificant(amount / 100_000_000)}亿`;
  if (abs >= 10_000) return `${formatSignificant(amount / 10_000)}万`;
  return amount.toLocaleString();
}

function comma(value: number) {
  return Math.trunc(value).toLocaleString();
}
