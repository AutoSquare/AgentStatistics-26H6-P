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

    <main id="main-content" ref="workspace" class="workspace" tabindex="-1">
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
          <button class="primary-button" :disabled="pageStatus('codex') === 'scanning'" @click="refreshCodex">
            <RefreshCw :size="17" :class="{ spinning: pageStatus('codex') === 'scanning' }" />
            刷新
          </button>
        </div>
        <div v-else-if="activePage === 'cursor'" class="topbar-actions compact-actions">
          <p class="auth-hint" :class="{ ready: cursorAuthAvailable, cache: !cursorAuthAvailable && cursorLocalCacheAvailable }">
            {{
              cursorAuthAvailable
                ? "已连接 Cursor 官网"
                : cursorLocalCacheAvailable
                  ? "未登录官网（仅本地缓存）"
                  : "未检测到 Cursor 登录态"
            }}
          </p>
          <button class="secondary-button" :disabled="pageStatus('cursor') === 'scanning'" @click="openCursorLogin">
            登录 / 切换账号
          </button>
          <button class="primary-button" :disabled="pageStatus('cursor') === 'scanning'" @click="refreshCursor">
            <RefreshCw :size="17" :class="{ spinning: pageStatus('cursor') === 'scanning' }" />
            刷新
          </button>
        </div>
        <div v-else-if="activePage === 'antigravity'" class="topbar-actions">
          <label class="path-field">
            <span>Antigravity cache</span>
            <input v-model="antigravityCacheDraft" type="text" spellcheck="false" @keyup.enter="saveAntigravityCachePath" />
          </label>
          <button class="secondary-button" @click="saveAntigravityCachePath">
            <FolderInput :size="17" />
            保存路径
          </button>
          <button class="primary-button" :disabled="pageStatus('antigravity') === 'scanning'" @click="refreshAntigravity">
            <RefreshCw :size="17" :class="{ spinning: pageStatus('antigravity') === 'scanning' }" />
            刷新
          </button>
        </div>
      </header>

      <AgentDashboard
        v-if="activePage === 'codex'"
        ref="codexDashboard"
        :payload="codexData"
        :active-range="codexRange"
        :status-kind="pageStatus('codex')"
        :status-message="pageMessage('codex')"
        :chart-width="chartWidth"
        :active="activePage === 'codex'"
        empty-title="等待 Codex 用量数据"
        empty-description="应用会监听本地 Codex 会话日志。也可以点击刷新立即扫描。"
        risk-caption="来自 Codex rate_limits"
        @update:active-range="codexRange = $event"
      />

      <AgentDashboard
        v-else-if="activePage === 'cursor'"
        ref="cursorDashboard"
        :payload="cursorData"
        :active-range="cursorRange"
        :status-kind="pageStatus('cursor')"
        :status-message="pageMessage('cursor')"
        :chart-width="chartWidth"
        :active="activePage === 'cursor'"
        empty-title="等待 Cursor 用量数据"
        empty-description="请先确认本机已登录 Cursor，然后点击刷新。"
        risk-caption="Cursor 额度"
        @update:active-range="cursorRange = $event"
      />

      <AgentDashboard
        v-else-if="activePage === 'antigravity'"
        ref="antigravityDashboard"
        :payload="antigravityData"
        :active-range="antigravityRange"
        :status-kind="pageStatus('antigravity')"
        :status-message="pageMessage('antigravity')"
        :chart-width="chartWidth"
        :active="activePage === 'antigravity'"
        empty-title="等待 Antigravity 用量数据"
        empty-description="刷新时会从运行中的 Antigravity CLI（agy）同步用量，并读取 ~/.gemini/antigravity-cli 的 transcript 与 antigravity-cache；CLI 未运行时仍可读取已有本地数据。"
        risk-caption="来自 Antigravity Connect RPC"
        @update:active-range="antigravityRange = $event"
      />

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
          <p>当 Codex、Cursor、Antigravity 与其他 Agent 适配器接入后，这里会汇总跨来源的 Token、请求、费用和趋势。</p>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Activity, FileText, FolderInput, Layers3, MousePointer2, Orbit, RefreshCw } from "@lucide/vue";
import AgentDashboard from "./components/AgentDashboard.vue";
import { onHostMessage, postToHost } from "./host";
import { buildAgentStatusMessage } from "./payloadStatus";
import type { AgentPayload, AgentSource, StatusKind } from "./types";

const navItems = [
  { id: "codex", label: "Codex", icon: Activity },
  { id: "cursor", label: "Cursor", icon: MousePointer2 },
  { id: "antigravity", label: "Antigravity", icon: Orbit },
  { id: "generic", label: "通用", icon: FileText },
  { id: "total", label: "总计", icon: Layers3 }
] as const;

type PageId = (typeof navItems)[number]["id"];

const activePage = ref<PageId>("codex");
const statusKind = ref<StatusKind>("idle");
const statusMessage = ref("等待宿主连接");
const pageStatuses = ref<Record<AgentSource, StatusKind>>({ codex: "idle", cursor: "idle", antigravity: "idle" });
const pageMessages = ref<Record<AgentSource, string>>({ codex: "等待数据", cursor: "等待数据", antigravity: "等待数据" });

const codexRootDraft = ref("");
const antigravityCacheDraft = ref("");
const cursorAuthAvailable = ref(false);
const cursorLocalCacheAvailable = ref(false);

const codexData = ref<AgentPayload | null>(null);
const cursorData = ref<AgentPayload | null>(null);
const antigravityData = ref<AgentPayload | null>(null);

const codexRange = ref("today");
const cursorRange = ref("today");
const antigravityRange = ref("today");

const sidebarWidth = ref(loadSidebarWidth());
const workspace = ref<HTMLElement | null>(null);
const codexDashboard = ref<InstanceType<typeof AgentDashboard> | null>(null);
const cursorDashboard = ref<InstanceType<typeof AgentDashboard> | null>(null);
const antigravityDashboard = ref<InstanceType<typeof AgentDashboard> | null>(null);
const chartWidth = ref(0);
let resizeObserver: ResizeObserver | null = null;
let resizeFrame = 0;

const pageTitle = computed(() => {
  if (activePage.value === "codex") return "Codex 用量统计";
  if (activePage.value === "cursor") return "Cursor 用量统计";
  if (activePage.value === "antigravity") return "Antigravity 用量统计";
  if (activePage.value === "generic") return "通用文件分析";
  return "跨 Agent 总计";
});

function pageStatus(source: AgentSource): StatusKind {
  return pageStatuses.value[source];
}

function pageMessage(source: AgentSource): string {
  return pageMessages.value[source];
}

onMounted(() => {
  onHostMessage((message) => {
    if (message.type === "settings") {
      if (typeof message.codexRoot === "string") codexRootDraft.value = message.codexRoot;
      if (typeof message.antigravityCachePath === "string") antigravityCacheDraft.value = message.antigravityCachePath;
      if (typeof message.cursorAuthAvailable === "boolean") cursorAuthAvailable.value = message.cursorAuthAvailable;
      if (typeof message.cursorLocalCacheAvailable === "boolean") cursorLocalCacheAvailable.value = message.cursorLocalCacheAvailable;
    }
    if (message.type === "status") {
      const source = typeof message.source === "string" ? (message.source as AgentSource) : null;
      const status = (message.status as StatusKind) ?? "idle";
      const text = typeof message.message === "string" ? message.message : "";
      if (source && (source === "codex" || source === "cursor" || source === "antigravity")) {
        pageStatuses.value[source] = status;
        pageMessages.value[source] = text;
      }
      statusKind.value = status;
      statusMessage.value = text;
    }
    if (message.type === "codexData") {
      const payload = message.payload as AgentPayload;
      codexData.value = payload;
      pageStatuses.value.codex = "idle";
      pageMessages.value.codex = buildAgentStatusMessage(payload);
    }
    if (message.type === "cursorData") {
      const payload = message.payload as AgentPayload;
      cursorData.value = payload;
      pageStatuses.value.cursor = "idle";
      pageMessages.value.cursor = buildAgentStatusMessage(payload);
    }
    if (message.type === "antigravityData") {
      const payload = message.payload as AgentPayload;
      antigravityData.value = payload;
      pageStatuses.value.antigravity = "idle";
      pageMessages.value.antigravity = buildAgentStatusMessage(payload);
    }
    if (message.type === "dashboardResize") {
      scheduleChartResize();
    }
  });
  postToHost({ type: "ready" });
  window.addEventListener("resize", scheduleChartResize);
  resizeObserver = new ResizeObserver(scheduleChartResize);
  if (workspace.value) resizeObserver.observe(workspace.value);
  scheduleChartResize();
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", scheduleChartResize);
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  stopResize();
});

watch(activePage, () => {
  scheduleChartResize();
});

function scheduleChartResize() {
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    chartWidth.value = workspace.value?.clientWidth ?? 0;
    codexDashboard.value?.scheduleChartResize();
    cursorDashboard.value?.scheduleChartResize();
    antigravityDashboard.value?.scheduleChartResize();
  });
}

function refreshCodex() {
  postToHost({ type: "refresh" });
}

function refreshCursor() {
  postToHost({ type: "refreshCursor" });
}

function openCursorLogin() {
  postToHost({ type: "openCursorLogin" });
}

function refreshAntigravity() {
  postToHost({ type: "refreshAntigravity" });
}

function saveCodexRoot() {
  postToHost({ type: "setCodexRoot", path: codexRootDraft.value });
}

function saveAntigravityCachePath() {
  postToHost({ type: "setAntigravityCachePath", path: antigravityCacheDraft.value });
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
  scheduleChartResize();
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
  scheduleChartResize();
}
</script>
