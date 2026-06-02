<template>
  <div class="app-shell" :style="{ '--sidebar-width': `${sidebarWidth}px` }">
    <a class="skip-link" href="#main-content">跳到主内容</a>
    <aside class="sidebar" aria-label="主导航">
      <div class="brand">
        <div class="brand-mark"><img src="/logo.png" alt="AgentStatistics logo" /></div>
        <div>
          <strong>AgentStatistics</strong>
          <span>AutoSquare</span>
        </div>
      </div>
      <nav class="nav-list">
        <button v-for="item in navItems" :key="item.id" class="nav-item" :class="{ active: activePage === item.id }" @click="activePage = item.id">
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-status">
        <span class="status-dot" :class="statusKind"></span>
        <span>{{ statusMessage }}</span>
      </div>
    </aside>
    <div
      class="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整导航栏宽度"
      tabindex="0"
      @mousedown="startResize"
      @keydown="resizeWithKeyboard"
    ></div>

    <main id="main-content" class="workspace" tabindex="-1">
      <header class="topbar">
        <div>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div v-if="activePage === 'codex'" class="topbar-actions">
          <label class="path-field">
            <span>Codex sessions</span>
            <input v-model="codexRootDraft" type="text" spellcheck="false" @keyup.enter="saveCodexRoot" />
          </label>
          <button class="secondary-button" @click="saveCodexRoot">
            <FolderInput :size="17" />
            保存路径
          </button>
          <button class="primary-button" :disabled="statusKind === 'scanning'" @click="refresh">
            <RefreshCw :size="17" :class="{ spinning: statusKind === 'scanning' }" />
            刷新
          </button>
        </div>
      </header>

      <section v-if="activePage === 'codex'" class="page">
        <div class="range-row">
          <div class="range-tabs" role="tablist" aria-label="统计范围">
            <button v-for="range in ranges" :key="range.id" :class="{ active: activeRange === range.id }" @click="activeRange = range.id">
              {{ range.label }}
            </button>
          </div>
          <div class="sync-meta">
            <span>{{ codexData ? `${codexData.generatedAt} 已同步` : "等待数据" }}</span>
            <button class="text-button" :disabled="!currentView" @click="exportCsv">
              <Download :size="16" />
              导出 CSV
            </button>
          </div>
        </div>

        <div v-if="!codexData && statusKind !== 'error'" class="empty-panel">
          <Database :size="34" />
          <h2>等待 Codex 用量数据</h2>
          <p>应用会监听本地 Codex 会话日志。也可以点击刷新立即扫描。</p>
        </div>

        <div v-else-if="statusKind === 'error'" class="empty-panel error">
          <TriangleAlert :size="34" />
          <h2>统计失败</h2>
          <p>{{ statusMessage }}</p>
        </div>

        <template v-else-if="currentView">
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
                  <p>来自 Codex rate_limits</p>
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

      <section v-else-if="activePage === 'generic'" class="page">
        <div class="empty-panel">
          <FileText :size="36" />
          <h2>通用统计暂未启用</h2>
          <p>后续会支持导入其他模型官网导出的 CSV 或统计文件，并接入大模型分析。第一阶段不实现 API Key 与模型调用。</p>
        </div>
      </section>

      <section v-else class="page">
        <div class="empty-panel">
          <Layers3 :size="36" />
          <h2>总计视图等待更多数据源</h2>
          <p>当 Codex、通用文件和其他 Agent 适配器接入后，这里会汇总跨来源的 Token、请求、费用和趋势。</p>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { Activity, Database, Download, FileText, FolderInput, Layers3, RefreshCw, TriangleAlert } from "@lucide/vue";
import { onHostMessage, postToHost } from "./host";
import { costOption, distributionOption, trendOption } from "./charts";
import type { CodexPayload, CodexView, StatusKind } from "./types";

const navItems = [
  { id: "codex", label: "Codex", icon: Activity },
  { id: "generic", label: "通用", icon: FileText },
  { id: "total", label: "总计", icon: Layers3 }
] as const;

const ranges = [
  { id: "today", label: "今天" },
  { id: "24h", label: "24 小时" },
  { id: "7", label: "7 天" },
  { id: "30", label: "30 天" },
  { id: "history", label: "历史" }
];

const activePage = ref<(typeof navItems)[number]["id"]>("codex");
const activeRange = ref("today");
const statusKind = ref<StatusKind>("idle");
const statusMessage = ref("等待宿主连接");
const codexRootDraft = ref("");
const codexData = ref<CodexPayload | null>(null);
const sidebarWidth = ref(loadSidebarWidth());
const trendChart = ref<HTMLElement | null>(null);
const distributionChart = ref<HTMLElement | null>(null);
const costChart = ref<HTMLElement | null>(null);
let trendInstance: echarts.ECharts | null = null;
let distributionInstance: echarts.ECharts | null = null;
let costInstance: echarts.ECharts | null = null;

const pageTitle = computed(() => {
  if (activePage.value === "codex") return "Codex 用量统计";
  if (activePage.value === "generic") return "通用文件分析";
  return "跨 Agent 总计";
});

const currentView = computed<CodexView | null>(() => codexData.value?.views?.[activeRange.value] ?? codexData.value?.views?.history ?? null);

onMounted(() => {
  onHostMessage((message) => {
    if (message.type === "settings" && typeof message.codexRoot === "string") {
      codexRootDraft.value = message.codexRoot;
    }
    if (message.type === "status") {
      statusKind.value = (message.status as StatusKind) ?? "idle";
      statusMessage.value = typeof message.message === "string" ? message.message : "";
    }
    if (message.type === "codexData") {
      codexData.value = message.payload as CodexPayload;
      statusKind.value = "idle";
      statusMessage.value = "已同步 Codex 用量";
    }
  });
  postToHost({ type: "ready" });
  window.addEventListener("resize", resizeCharts);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeCharts);
  stopResize();
});

watch(currentView, () => {
  void renderCharts();
});

watch(activePage, () => {
  void renderCharts();
});

async function renderCharts() {
  await nextTick();
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  if (!currentView.value || activePage.value !== "codex") return;
  if (trendChart.value) {
    trendInstance = ensureChartInstance(trendChart.value, trendInstance);
    trendInstance.setOption(trendOption(currentView.value), true);
  }
  if (distributionChart.value) {
    distributionInstance = ensureChartInstance(distributionChart.value, distributionInstance);
    distributionInstance.setOption(distributionOption(currentView.value), true);
  }
  if (costChart.value) {
    costInstance = ensureChartInstance(costChart.value, costInstance);
    costInstance.setOption(costOption(currentView.value), true);
  }
  resizeCharts();
}

function ensureChartInstance(element: HTMLElement, instance: echarts.ECharts | null) {
  if (instance && instance.getDom() === element) return instance;
  instance?.dispose();
  return echarts.init(element);
}

function resizeCharts() {
  trendInstance?.resize();
  distributionInstance?.resize();
  costInstance?.resize();
}

function refresh() {
  postToHost({ type: "refresh" });
}

function saveCodexRoot() {
  postToHost({ type: "setCodexRoot", path: codexRootDraft.value });
}

function exportCsv() {
  if (!codexData.value || !currentView.value) return;
  const rows = codexData.value.records.filter((row) => row[0] >= currentView.value!.range.start && row[0] <= currentView.value!.range.end);
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
  link.download = `usage-events-${currentView.value.key}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function priceRecord(row: [number, string, string, number, number, number, number, number]) {
  const rules = codexData.value?.pricingRules ?? [];
  const model = row[2].toLowerCase();
  const rule = rules.find((item) => item.patterns.some((pattern) => model.includes(pattern)));
  if (!rule) return 0;
  const cached = Math.min(row[3] || 0, row[4] || 0);
  const input = Math.max(0, (row[3] || 0) - cached);
  const output = row[5] || 0;
  const reasoning = row[6] || 0;
  return (input * rule.input + cached * rule.cached + (output + reasoning) * rule.output) / 1_000_000;
}

function csvCell(value: unknown) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replaceAll("\"", "\"\"")}"` : text;
}

function loadSidebarWidth() {
  const raw = Number(localStorage.getItem("agentstatistics.sidebarWidth"));
  return Number.isFinite(raw) ? clampSidebarWidth(raw) : 248;
}

function persistSidebarWidth() {
  localStorage.setItem("agentstatistics.sidebarWidth", String(sidebarWidth.value));
}

function clampSidebarWidth(value: number) {
  return Math.min(360, Math.max(200, Math.round(value)));
}

function startResize(event: MouseEvent) {
  event.preventDefault();
  document.body.classList.add("is-resizing-sidebar");
  window.addEventListener("mousemove", resizeSidebar);
  window.addEventListener("mouseup", stopResize);
}

function resizeSidebar(event: MouseEvent) {
  sidebarWidth.value = clampSidebarWidth(event.clientX);
  persistSidebarWidth();
  resizeCharts();
}

function stopResize() {
  document.body.classList.remove("is-resizing-sidebar");
  window.removeEventListener("mousemove", resizeSidebar);
  window.removeEventListener("mouseup", stopResize);
}

function resizeWithKeyboard(event: KeyboardEvent) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
  event.preventDefault();
  if (event.key === "Home") sidebarWidth.value = 200;
  else if (event.key === "End") sidebarWidth.value = 360;
  else sidebarWidth.value = clampSidebarWidth(sidebarWidth.value + (event.key === "ArrowRight" ? 12 : -12));
  persistSidebarWidth();
  resizeCharts();
}
</script>
