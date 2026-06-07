<template>
  <section class="page">
    <div class="range-row">
      <div class="range-tabs" role="tablist" aria-label="统计范围">
        <button v-for="range in ranges" :key="range.id" :class="{ active: activeRange === range.id }" @click="$emit('update:activeRange', range.id)">
          {{ range.label }}
        </button>
      </div>
      <div class="sync-meta">
        <span>{{ syncMetaLabel }}</span>
        <button class="text-button" :disabled="!currentView" @click="exportCsv">
          <Download :size="16" />
          导出 CSV
        </button>
      </div>
    </div>

    <section v-if="accountOptions.length" class="account-section" aria-label="Cursor 账号用量">
      <button
        class="account-card all-account"
        :class="{ active: selectedAccountId === 'all' }"
        type="button"
        @click="selectedAccountId = 'all'"
      >
        <span class="account-name">全部账号</span>
        <strong>{{ allAccountsView?.summary.totalTokensLabel ?? "0" }}</strong>
        <small>{{ allAccountsView?.summary.requestsLabel ?? "0" }} 次调用 · ${{ (allAccountsView?.cost.total ?? 0).toFixed(2) }}</small>
      </button>
      <div class="account-strip" aria-label="Cursor 账号列表">
        <button
          v-for="account in sortedAccountOptions"
          :key="account.id"
          class="account-card account-chip"
          :class="{ active: selectedAccountId === account.id }"
          type="button"
          @click="selectedAccountId = account.id"
        >
          <span class="account-name">
            <span class="account-label">{{ account.label }}</span>
            <b v-if="account.isCurrent">当前</b>
            <b v-else-if="payload?.activeAccountId === account.id">同步</b>
            <b v-else>离线</b>
          </span>
          <strong>{{ account.views[activeRange]?.summary.totalTokensLabel ?? "0" }}</strong>
          <small>
            ID …{{ account.idSuffix }} · {{ account.views[activeRange]?.summary.requestsLabel ?? "0" }} 次调用
            <em v-if="account.isOnline && account.syncStatus !== 'ok'">同步异常</em>
          </small>
        </button>
      </div>
    </section>

    <div v-if="showEmptyPanel" class="empty-panel">
      <Database :size="34" />
      <h2>{{ emptyTitle }}</h2>
      <p>{{ resolvedEmptyDescription }}</p>
    </div>

    <div v-else-if="statusKind === 'error'" class="empty-panel error">
      <TriangleAlert :size="34" />
      <h2>统计失败</h2>
      <p>{{ statusMessage }}</p>
    </div>

    <template v-else-if="currentView && hasUsageData">
      <section class="kpi-grid">
        <article class="kpi-card primary">
          <span>总 Token</span>
          <strong>{{ currentView.summary.totalTokensLabel }}</strong>
          <small>{{ currentView.summary.requestsLabel }} 次调用 · 峰值 {{ currentView.summary.peakTpmLabel }}</small>
        </article>
        <article class="kpi-card">
          <span>输入</span>
          <strong>{{ currentView.summary.inputLabel }}</strong>
          <small>缓存 {{ currentView.summary.cachedLabel }}</small>
        </article>
        <article class="kpi-card">
          <span>输出</span>
          <strong>{{ currentView.summary.outputLabel }}</strong>
          <small>推理 {{ currentView.summary.reasoningLabel }}</small>
        </article>
        <article class="kpi-card">
          <span>缓存命中</span>
          <strong>{{ currentView.summary.cacheHitLabel }}</strong>
          <small>失败 {{ currentView.summary.failures }} · 成功 {{ currentView.summary.successRateLabel }}</small>
        </article>
        <article class="kpi-card accent">
          <span>估算费用</span>
          <strong>${{ currentView.cost.total.toFixed(2) }}</strong>
          <small>单次均值 ${{ currentView.cost.average.toFixed(2) }}</small>
        </article>
      </section>

      <section class="dashboard-grid">
        <article class="panel wide">
          <div class="panel-head">
            <div>
              <h2>Token 趋势</h2>
              <p>{{ currentView.label }} · 累计 {{ currentView.summary.totalTokensLabel }}</p>
            </div>
          </div>
          <div ref="trendChart" class="chart"></div>
        </article>
        <article class="panel">
          <div class="panel-head">
            <div>
              <h2>额度与风险</h2>
              <p>{{ riskCaption }}</p>
            </div>
          </div>
          <div class="risk-list">
            <div v-for="risk in currentView.risk" :key="risk.name" class="risk-row">
              <div>
                <strong>{{ risk.name }}</strong>
                <span>{{ risk.note }}</span>
              </div>
              <b>{{ risk.percentLabel || risk.label }}</b>
              <div class="risk-bar"><span :style="{ width: `${Math.min(100, Math.max(0, risk.value || 0))}%` }"></span></div>
            </div>
          </div>
        </article>
        <article class="panel wide">
          <div class="panel-head">
            <div>
              <h2>调用分布</h2>
              <p>Token 与请求数按时间桶聚合</p>
            </div>
          </div>
          <div ref="distributionChart" class="chart"></div>
        </article>
        <article class="panel">
          <div class="panel-head">
            <div>
              <h2>费用结构</h2>
              <p>本地 Token 估算，不代表官方账单</p>
            </div>
          </div>
          <div ref="costChart" class="chart small"></div>
        </article>
      </section>

      <section class="tables-grid">
        <article class="panel table-panel">
          <div class="panel-head">
            <div>
              <h2>会话 / 项目排行</h2>
              <p>按 Token 消耗排序</p>
            </div>
          </div>
          <div class="data-table">
            <div class="table-head session"><span>项目</span><span>模型</span><span>Token</span><span>调用</span></div>
            <div v-for="row in currentView.sessions" :key="`${row.rank}-${row.name}`" class="table-row session">
              <span><b>{{ row.rank }}.</b> {{ row.name }}</span>
              <span class="pill">{{ row.model }}</span>
              <span>{{ row.tokensLabel }}</span>
              <span>{{ row.requests }}</span>
            </div>
          </div>
        </article>
        <article class="panel table-panel">
          <div class="panel-head">
            <div>
              <h2>模型排行</h2>
              <p>Token、费用与延迟</p>
            </div>
          </div>
          <div class="data-table">
            <div class="table-head model"><span>模型</span><span>Token</span><span>费用</span><span>TTFB</span></div>
            <div v-for="row in currentView.models" :key="row.name" class="table-row model">
              <span class="model-name">{{ row.name }}</span>
              <span>{{ row.tokensLabel }}</span>
              <span>${{ row.cost.toFixed(2) }}</span>
              <span>{{ row.latencyLabel }}</span>
            </div>
          </div>
        </article>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { Database, Download, TriangleAlert } from "@lucide/vue";
import { costOption, distributionOption, trendOption } from "../charts";
import type { ChartView, TimeGranularity } from "../charts";
import { buildEmptyDescription } from "../payloadStatus";
import type { AgentPayload, AgentView, CursorAccountUsage, StatusKind } from "../types";

const props = defineProps<{
  payload: AgentPayload | null;
  activeRange: string;
  statusKind: StatusKind;
  statusMessage: string;
  emptyTitle: string;
  emptyDescription: string;
  riskCaption: string;
  chartWidth: number;
  active: boolean;
}>();

defineEmits<{
  "update:activeRange": [value: string];
}>();

const ranges = [
  { id: "today", label: "今天" },
  { id: "24h", label: "24 小时" },
  { id: "7", label: "7 天" },
  { id: "30", label: "30 天" },
  { id: "history", label: "历史" }
];

const trendChart = ref<HTMLElement | null>(null);
const distributionChart = ref<HTMLElement | null>(null);
const costChart = ref<HTMLElement | null>(null);
const selectedAccountId = ref("all");
const lastActiveAccountId = ref<string | null>(null);
let trendInstance: echarts.ECharts | null = null;
let distributionInstance: echarts.ECharts | null = null;
let costInstance: echarts.ECharts | null = null;
let resizeObserver: ResizeObserver | null = null;
let resizeFrame = 0;

const accountOptions = computed<CursorAccountUsage[]>(() => props.payload?.accounts ?? []);
const sortedAccountOptions = computed<CursorAccountUsage[]>(() =>
  accountOptions.value
    .map((account, index) => ({ account, index }))
    .sort((left, right) => {
      if (left.account.isCurrent !== right.account.isCurrent) return left.account.isCurrent ? -1 : 1;
      return left.index - right.index;
    })
    .map((item) => item.account)
);
const selectedAccount = computed(() => accountOptions.value.find((item) => item.id === selectedAccountId.value) ?? null);
const currentAccountId = computed(() => {
  const activeAccountId = props.payload?.activeAccountId;
  if (activeAccountId && accountOptions.value.some((item) => item.id === activeAccountId)) return activeAccountId;
  return accountOptions.value.find((item) => item.isCurrent)?.id ?? null;
});
const effectiveRecords = computed(() => selectedAccount.value?.records ?? props.payload?.records ?? []);
const hasUsageData = computed(() => effectiveRecords.value.length > 0);
const showEmptyPanel = computed(() => props.statusKind !== "error" && (!props.payload || !hasUsageData.value));
const resolvedEmptyDescription = computed(() => {
  if (!props.payload) return props.emptyDescription;
  return buildEmptyDescription(props.payload, props.emptyDescription);
});
const syncMetaLabel = computed(() => {
  if (!props.payload) return "等待数据";
  if (hasUsageData.value) return `${props.payload.generatedAt} 已同步`;
  return `${props.payload.generatedAt} 扫描完成（无用量）`;
});

const allAccountsView = computed<AgentView | null>(() => props.payload?.views?.[props.activeRange] ?? props.payload?.views?.history ?? null);
const currentView = computed<AgentView | null>(() => {
  const views = selectedAccount.value?.views ?? props.payload?.views;
  return views?.[props.activeRange] ?? views?.history ?? null;
});
const chartView = computed<ChartView | null>(() => {
  if (!currentView.value) return null;
  if (props.activeRange !== "history" || !props.payload) return currentView.value;
  return {
    ...currentView.value,
    ...buildHistoryCharts(effectiveRecords.value, currentView.value.range.start, currentView.value.range.end, props.chartWidth)
  };
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  trendInstance?.dispose();
  distributionInstance?.dispose();
  costInstance?.dispose();
});

async function renderCharts() {
  if (!props.active) return;
  await nextTick();
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  if (!chartView.value) return;
  if (trendChart.value) {
    trendInstance = ensureChartInstance(trendChart.value, trendInstance);
    trendInstance.setOption(trendOption(chartView.value), true);
  }
  if (distributionChart.value) {
    distributionInstance = ensureChartInstance(distributionChart.value, distributionInstance);
    distributionInstance.setOption(distributionOption(chartView.value), true);
  }
  if (costChart.value) {
    costInstance = ensureChartInstance(costChart.value, costInstance);
    costInstance.setOption(costOption(chartView.value), true);
  }
  scheduleChartResize();
}

function tryRenderCharts() {
  if (!props.active || !chartView.value) return;
  void renderCharts();
}

watch(chartView, tryRenderCharts, { immediate: true });

watch(() => props.active, tryRenderCharts, { immediate: true });

watch(
  currentAccountId,
  (accountId) => {
    const selectedExists = selectedAccountId.value === "all" || accountOptions.value.some((item) => item.id === selectedAccountId.value);
    if (!accountId) {
      if (!selectedExists) selectedAccountId.value = "all";
      lastActiveAccountId.value = null;
      return;
    }
    if (!selectedExists || lastActiveAccountId.value !== accountId) {
      selectedAccountId.value = accountId;
    }
    lastActiveAccountId.value = accountId;
  },
  { immediate: true }
);

watch(
  () => props.chartWidth,
  () => {
    if (props.active) scheduleChartResize();
  }
);

onMounted(() => {
  resizeObserver = new ResizeObserver(scheduleChartResize);
  [trendChart.value, distributionChart.value, costChart.value].forEach((element) => {
    if (element) resizeObserver?.observe(element);
  });
  tryRenderCharts();
});

function ensureChartInstance(element: HTMLElement, instance: echarts.ECharts | null) {
  if (instance && instance.getDom() === element) return instance;
  instance?.dispose();
  return echarts.init(element);
}

function scheduleChartResize() {
  if (!props.active) return;
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    trendInstance?.resize();
    distributionInstance?.resize();
    costInstance?.resize();
  });
}

function exportCsv() {
  if (!props.payload || !currentView.value) return;
  const rows = effectiveRecords.value.filter((row) => row[0] >= currentView.value!.range.start && row[0] <= currentView.value!.range.end);
  const headers = ["Date", "Cloud Agent ID", "Automation ID", "Kind", "Model", "Max Mode", "Input (w/ Cache Write)", "Input (w/o Cache Write)", "Cache Read", "Output Tokens", "Total Tokens", "Cost"];
  const body = rows.map((row) => {
    const input = row[3] || 0;
    const cached = Math.min(input, row[4] || 0);
    const inputWithoutCache = Math.max(0, input - cached);
    const output = (row[5] || 0) + (row[6] || 0);
    const cost = priceRecord(row).toFixed(2);
    return [new Date(row[0]).toISOString(), "", "", "", row[2], "", input, inputWithoutCache, cached, output, row[7], cost].map(csvCell).join(",");
  });
  const blob = new Blob([[headers.join(","), ...body].join("\r\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  const accountSuffix = selectedAccount.value ? `-${selectedAccount.value.idSuffix}` : "";
  link.download = `${props.payload.source}-usage-events${accountSuffix}-${currentView.value.key}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function priceRecord(row: AgentPayload["records"][number]) {
  if (typeof row[8] === "number" && Number.isFinite(row[8])) return row[8];
  const rules = props.payload?.pricingRules ?? [];
  const model = row[2].toLowerCase();
  const rule = rules.find((item) => item.patterns.some((pattern) => model.includes(pattern)));
  if (!rule) return 0;
  const cached = Math.min(row[3] || 0, row[4] || 0);
  const input = Math.max(0, (row[3] || 0) - cached);
  const output = row[5] || 0;
  const reasoning = row[6] || 0;
  return (input * rule.input + cached * rule.cached + (output + reasoning) * rule.output) / 1_000_000;
}

function buildHistoryCharts(records: AgentPayload["records"], start: number, end: number, width: number) {
  const rows = records.filter((row) => row[0] >= start && row[0] <= end);
  const granularity = chooseHistoryGranularity(start, end, targetBucketCount(width));
  const bucketMap = new Map<number, { trend: [number, number, number, number, number, number, number, number]; distribution: [number, number, number, number] }>();

  for (const row of rows) {
    const ts = bucketStart(row[0], granularity);
    const bucket =
      bucketMap.get(ts) ??
      {
        trend: [ts, 0, 0, 0, 0, 0, 0, 0],
        distribution: [ts, 0, 0, 0]
      };
    const cost = priceRecord(row);
    const input = row[3] || 0;
    const cached = Math.min(input, row[4] || 0);
    bucket.trend[1] += row[7] || 0;
    bucket.trend[2] += cached;
    bucket.trend[3] += row[5] || 0;
    bucket.trend[4] += input - cached;
    bucket.trend[5] += row[6] || 0;
    bucket.trend[6] += 1;
    bucket.trend[7] += cost;
    bucket.distribution[1] += row[7] || 0;
    bucket.distribution[2] += 1;
    bucket.distribution[3] += cost;
    bucketMap.set(ts, bucket);
  }

  const buckets = Array.from(bucketMap.values()).sort((a, b) => a.trend[0] - b.trend[0]);
  return {
    axisGranularity: granularity,
    trend: buckets.map((bucket) => [bucket.trend[0], bucket.trend[1], bucket.trend[2], bucket.trend[3], bucket.trend[4], bucket.trend[5], bucket.trend[6], Number(bucket.trend[7].toFixed(6))] as [number, number, number, number, number, number, number, number]),
    distribution: buckets.map((bucket) => [bucket.distribution[0], bucket.distribution[1], bucket.distribution[2], Number(bucket.distribution[3].toFixed(6))] as [number, number, number, number])
  };
}

function targetBucketCount(width: number) {
  const safeWidth = width > 0 ? width : 900;
  return Math.max(36, Math.min(180, Math.round(safeWidth / 10)));
}

function chooseHistoryGranularity(start: number, end: number, targetBuckets: number): TimeGranularity {
  const duration = Math.max(1, end - start);
  if (Math.ceil(duration / 3_600_000) <= targetBuckets) return "hour";
  if (Math.ceil(duration / 86_400_000) <= targetBuckets) return "day";
  if (monthSpan(start, end) <= targetBuckets) return "month";
  return "year";
}

function bucketStart(ts: number, granularity: TimeGranularity) {
  const date = new Date(ts);
  if (granularity === "year") return new Date(date.getFullYear(), 0, 1).getTime();
  if (granularity === "month") return new Date(date.getFullYear(), date.getMonth(), 1).getTime();
  if (granularity === "day") return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (granularity === "hour") return new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()).getTime();
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours(), date.getMinutes()).getTime();
}

function monthSpan(start: number, end: number) {
  const startDate = new Date(start);
  const endDate = new Date(end);
  return Math.max(1, (endDate.getFullYear() - startDate.getFullYear()) * 12 + endDate.getMonth() - startDate.getMonth() + 1);
}

function csvCell(value: unknown) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replaceAll("\"", "\"\"")}"` : text;
}

defineExpose({ scheduleChartResize });
</script>
