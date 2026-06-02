import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { Activity, Database, Download, FileText, FolderInput, Layers3, RefreshCw, TriangleAlert } from "@lucide/vue";
import { onHostMessage, postToHost } from "./host";
import { costOption, distributionOption, trendOption } from "./charts";
const navItems = [
    { id: "codex", label: "Codex", icon: Activity },
    { id: "generic", label: "通用", icon: FileText },
    { id: "total", label: "总计", icon: Layers3 }
];
const ranges = [
    { id: "today", label: "今天" },
    { id: "24h", label: "24 小时" },
    { id: "7", label: "7 天" },
    { id: "30", label: "30 天" },
    { id: "history", label: "历史" }
];
const activePage = ref("codex");
const activeRange = ref("today");
const statusKind = ref("idle");
const statusMessage = ref("等待宿主连接");
const codexRootDraft = ref("");
const codexData = ref(null);
const sidebarWidth = ref(loadSidebarWidth());
const trendChart = ref(null);
const distributionChart = ref(null);
const costChart = ref(null);
let trendInstance = null;
let distributionInstance = null;
let costInstance = null;
const pageTitle = computed(() => {
    if (activePage.value === "codex")
        return "Codex 用量统计";
    if (activePage.value === "generic")
        return "通用文件分析";
    return "跨 Agent 总计";
});
const currentView = computed(() => codexData.value?.views?.[activeRange.value] ?? codexData.value?.views?.history ?? null);
onMounted(() => {
    onHostMessage((message) => {
        if (message.type === "settings" && typeof message.codexRoot === "string") {
            codexRootDraft.value = message.codexRoot;
        }
        if (message.type === "status") {
            statusKind.value = message.status ?? "idle";
            statusMessage.value = typeof message.message === "string" ? message.message : "";
        }
        if (message.type === "codexData") {
            codexData.value = message.payload;
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
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    if (!currentView.value || activePage.value !== "codex")
        return;
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
function ensureChartInstance(element, instance) {
    if (instance && instance.getDom() === element)
        return instance;
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
    if (!codexData.value || !currentView.value)
        return;
    const rows = codexData.value.records.filter((row) => row[0] >= currentView.value.range.start && row[0] <= currentView.value.range.end);
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
function priceRecord(row) {
    const rules = codexData.value?.pricingRules ?? [];
    const model = row[2].toLowerCase();
    const rule = rules.find((item) => item.patterns.some((pattern) => model.includes(pattern)));
    if (!rule)
        return 0;
    const cached = Math.min(row[3] || 0, row[4] || 0);
    const input = Math.max(0, (row[3] || 0) - cached);
    const output = row[5] || 0;
    const reasoning = row[6] || 0;
    return (input * rule.input + cached * rule.cached + (output + reasoning) * rule.output) / 1_000_000;
}
function csvCell(value) {
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
function clampSidebarWidth(value) {
    return Math.min(360, Math.max(200, Math.round(value)));
}
function startResize(event) {
    event.preventDefault();
    document.body.classList.add("is-resizing-sidebar");
    window.addEventListener("mousemove", resizeSidebar);
    window.addEventListener("mouseup", stopResize);
}
function resizeSidebar(event) {
    sidebarWidth.value = clampSidebarWidth(event.clientX);
    persistSidebarWidth();
    resizeCharts();
}
function stopResize() {
    document.body.classList.remove("is-resizing-sidebar");
    window.removeEventListener("mousemove", resizeSidebar);
    window.removeEventListener("mouseup", stopResize);
}
function resizeWithKeyboard(event) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End")
        return;
    event.preventDefault();
    if (event.key === "Home")
        sidebarWidth.value = 200;
    else if (event.key === "End")
        sidebarWidth.value = 360;
    else
        sidebarWidth.value = clampSidebarWidth(sidebarWidth.value + (event.key === "ArrowRight" ? 12 : -12));
    persistSidebarWidth();
    resizeCharts();
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "app-shell" },
    ...{ style: ({ '--sidebar-width': `${__VLS_ctx.sidebarWidth}px` }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.a, __VLS_intrinsicElements.a)({
    ...{ class: "skip-link" },
    href: "#main-content",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.aside, __VLS_intrinsicElements.aside)({
    ...{ class: "sidebar" },
    'aria-label': "主导航",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "brand" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "brand-mark" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
    src: "/logo.png",
    alt: "AgentStatistics logo",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.nav, __VLS_intrinsicElements.nav)({
    ...{ class: "nav-list" },
});
for (const [item] of __VLS_getVForSourceType((__VLS_ctx.navItems))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.activePage = item.id;
            } },
        key: (item.id),
        ...{ class: "nav-item" },
        ...{ class: ({ active: __VLS_ctx.activePage === item.id }) },
    });
    const __VLS_0 = ((item.icon));
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
        size: (18),
    }));
    const __VLS_2 = __VLS_1({
        size: (18),
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (item.label);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "sidebar-status" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "status-dot" },
    ...{ class: (__VLS_ctx.statusKind) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
(__VLS_ctx.statusMessage);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onMousedown: (__VLS_ctx.startResize) },
    ...{ onKeydown: (__VLS_ctx.resizeWithKeyboard) },
    ...{ class: "sidebar-resizer" },
    role: "separator",
    'aria-orientation': "vertical",
    'aria-label': "调整导航栏宽度",
    tabindex: "0",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.main, __VLS_intrinsicElements.main)({
    id: "main-content",
    ...{ class: "workspace" },
    tabindex: "-1",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.header, __VLS_intrinsicElements.header)({
    ...{ class: "topbar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h1, __VLS_intrinsicElements.h1)({});
(__VLS_ctx.pageTitle);
if (__VLS_ctx.activePage === 'codex') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "topbar-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "path-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onKeyup: (__VLS_ctx.saveCodexRoot) },
        value: (__VLS_ctx.codexRootDraft),
        type: "text",
        spellcheck: "false",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.saveCodexRoot) },
        ...{ class: "secondary-button" },
    });
    const __VLS_4 = {}.FolderInput;
    /** @type {[typeof __VLS_components.FolderInput, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        size: (17),
    }));
    const __VLS_6 = __VLS_5({
        size: (17),
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.refresh) },
        ...{ class: "primary-button" },
        disabled: (__VLS_ctx.statusKind === 'scanning'),
    });
    const __VLS_8 = {}.RefreshCw;
    /** @type {[typeof __VLS_components.RefreshCw, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.statusKind === 'scanning' }) },
    }));
    const __VLS_10 = __VLS_9({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.statusKind === 'scanning' }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
}
if (__VLS_ctx.activePage === 'codex') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "page" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "range-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "range-tabs" },
        role: "tablist",
        'aria-label': "统计范围",
    });
    for (const [range] of __VLS_getVForSourceType((__VLS_ctx.ranges))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.activePage === 'codex'))
                        return;
                    __VLS_ctx.activeRange = range.id;
                } },
            key: (range.id),
            ...{ class: ({ active: __VLS_ctx.activeRange === range.id }) },
        });
        (range.label);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "sync-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.codexData ? `${__VLS_ctx.codexData.generatedAt} 已同步` : "等待数据");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.exportCsv) },
        ...{ class: "text-button" },
        disabled: (!__VLS_ctx.currentView),
    });
    const __VLS_12 = {}.Download;
    /** @type {[typeof __VLS_components.Download, ]} */ ;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        size: (16),
    }));
    const __VLS_14 = __VLS_13({
        size: (16),
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
    if (!__VLS_ctx.codexData && __VLS_ctx.statusKind !== 'error') {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "empty-panel" },
        });
        const __VLS_16 = {}.Database;
        /** @type {[typeof __VLS_components.Database, ]} */ ;
        // @ts-ignore
        const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
            size: (34),
        }));
        const __VLS_18 = __VLS_17({
            size: (34),
        }, ...__VLS_functionalComponentArgsRest(__VLS_17));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    }
    else if (__VLS_ctx.statusKind === 'error') {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "empty-panel error" },
        });
        const __VLS_20 = {}.TriangleAlert;
        /** @type {[typeof __VLS_components.TriangleAlert, ]} */ ;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
            size: (34),
        }));
        const __VLS_22 = __VLS_21({
            size: (34),
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        (__VLS_ctx.statusMessage);
    }
    else if (__VLS_ctx.currentView) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
            ...{ class: "kpi-grid" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "kpi-card primary" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (__VLS_ctx.currentView.summary.totalTokensLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (__VLS_ctx.currentView.summary.requestsLabel);
        (__VLS_ctx.currentView.summary.peakTpmLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "kpi-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (__VLS_ctx.currentView.summary.inputLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (__VLS_ctx.currentView.summary.cachedLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "kpi-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (__VLS_ctx.currentView.summary.outputLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (__VLS_ctx.currentView.summary.reasoningLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "kpi-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (__VLS_ctx.currentView.summary.cacheHitLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (__VLS_ctx.currentView.summary.failures);
        (__VLS_ctx.currentView.summary.successRateLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "kpi-card accent" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (__VLS_ctx.currentView.cost.total.toFixed(2));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (__VLS_ctx.currentView.cost.average.toFixed(2));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
            ...{ class: "dashboard-grid" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel wide" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        (__VLS_ctx.currentView.label);
        (__VLS_ctx.currentView.summary.totalTokensLabel);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ref: "trendChart",
            ...{ class: "chart" },
        });
        /** @type {typeof __VLS_ctx.trendChart} */ ;
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "risk-list" },
        });
        for (const [risk] of __VLS_getVForSourceType((__VLS_ctx.currentView.risk))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                key: (risk.name),
                ...{ class: "risk-row" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
            (risk.name);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (risk.note);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
            (risk.percentLabel || risk.label);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "risk-bar" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ style: ({ width: `${Math.min(100, Math.max(0, risk.value || 0))}%` }) },
            });
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel wide" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ref: "distributionChart",
            ...{ class: "chart" },
        });
        /** @type {typeof __VLS_ctx.distributionChart} */ ;
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ref: "costChart",
            ...{ class: "chart small" },
        });
        /** @type {typeof __VLS_ctx.costChart} */ ;
        __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
            ...{ class: "tables-grid" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel table-panel" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "data-table" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "table-head session" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        for (const [row] of __VLS_getVForSourceType((__VLS_ctx.currentView.sessions))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                key: (`${row.rank}-${row.name}`),
                ...{ class: "table-row session" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
            (row.rank);
            (row.name);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "pill" },
            });
            (row.model);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (row.tokensLabel);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (row.requests);
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.article, __VLS_intrinsicElements.article)({
            ...{ class: "panel table-panel" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "panel-head" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "data-table" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "table-head model" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        for (const [row] of __VLS_getVForSourceType((__VLS_ctx.currentView.models))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                key: (row.name),
                ...{ class: "table-row model" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "model-name" },
            });
            (row.name);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (row.tokensLabel);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (row.cost.toFixed(2));
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            (row.latencyLabel);
        }
    }
}
else if (__VLS_ctx.activePage === 'generic') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "page" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-panel" },
    });
    const __VLS_24 = {}.FileText;
    /** @type {[typeof __VLS_components.FileText, ]} */ ;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent(__VLS_24, new __VLS_24({
        size: (36),
    }));
    const __VLS_26 = __VLS_25({
        size: (36),
    }, ...__VLS_functionalComponentArgsRest(__VLS_25));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "page" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-panel" },
    });
    const __VLS_28 = {}.Layers3;
    /** @type {[typeof __VLS_components.Layers3, ]} */ ;
    // @ts-ignore
    const __VLS_29 = __VLS_asFunctionalComponent(__VLS_28, new __VLS_28({
        size: (36),
    }));
    const __VLS_30 = __VLS_29({
        size: (36),
    }, ...__VLS_functionalComponentArgsRest(__VLS_29));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
}
/** @type {__VLS_StyleScopedClasses['app-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['skip-link']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar']} */ ;
/** @type {__VLS_StyleScopedClasses['brand']} */ ;
/** @type {__VLS_StyleScopedClasses['brand-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-list']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-status']} */ ;
/** @type {__VLS_StyleScopedClasses['status-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-resizer']} */ ;
/** @type {__VLS_StyleScopedClasses['workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['topbar']} */ ;
/** @type {__VLS_StyleScopedClasses['topbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['path-field']} */ ;
/** @type {__VLS_StyleScopedClasses['secondary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['primary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['range-row']} */ ;
/** @type {__VLS_StyleScopedClasses['range-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['sync-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['text-button']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['error']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-card']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-card']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-card']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-card']} */ ;
/** @type {__VLS_StyleScopedClasses['kpi-card']} */ ;
/** @type {__VLS_StyleScopedClasses['accent']} */ ;
/** @type {__VLS_StyleScopedClasses['dashboard-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['wide']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['chart']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['risk-list']} */ ;
/** @type {__VLS_StyleScopedClasses['risk-row']} */ ;
/** @type {__VLS_StyleScopedClasses['risk-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['wide']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['chart']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['chart']} */ ;
/** @type {__VLS_StyleScopedClasses['small']} */ ;
/** @type {__VLS_StyleScopedClasses['tables-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['table-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['data-table']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['session']} */ ;
/** @type {__VLS_StyleScopedClasses['table-row']} */ ;
/** @type {__VLS_StyleScopedClasses['session']} */ ;
/** @type {__VLS_StyleScopedClasses['pill']} */ ;
/** @type {__VLS_StyleScopedClasses['panel']} */ ;
/** @type {__VLS_StyleScopedClasses['table-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-head']} */ ;
/** @type {__VLS_StyleScopedClasses['data-table']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['model']} */ ;
/** @type {__VLS_StyleScopedClasses['table-row']} */ ;
/** @type {__VLS_StyleScopedClasses['model']} */ ;
/** @type {__VLS_StyleScopedClasses['model-name']} */ ;
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            Database: Database,
            Download: Download,
            FileText: FileText,
            FolderInput: FolderInput,
            Layers3: Layers3,
            RefreshCw: RefreshCw,
            TriangleAlert: TriangleAlert,
            navItems: navItems,
            ranges: ranges,
            activePage: activePage,
            activeRange: activeRange,
            statusKind: statusKind,
            statusMessage: statusMessage,
            codexRootDraft: codexRootDraft,
            codexData: codexData,
            sidebarWidth: sidebarWidth,
            trendChart: trendChart,
            distributionChart: distributionChart,
            costChart: costChart,
            pageTitle: pageTitle,
            currentView: currentView,
            refresh: refresh,
            saveCodexRoot: saveCodexRoot,
            exportCsv: exportCsv,
            startResize: startResize,
            resizeWithKeyboard: resizeWithKeyboard,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
//# sourceMappingURL=App.vue.js.map