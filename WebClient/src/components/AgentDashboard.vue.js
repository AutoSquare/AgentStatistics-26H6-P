import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";
import { Database, Download, TriangleAlert } from "@lucide/vue";
import { costOption, distributionOption, trendOption } from "../charts";
import { buildEmptyDescription } from "../payloadStatus";
const props = defineProps();
const __VLS_emit = defineEmits();
const ranges = [
    { id: "today", label: "今天" },
    { id: "24h", label: "24 小时" },
    { id: "7", label: "7 天" },
    { id: "30", label: "30 天" },
    { id: "history", label: "历史" }
];
const trendChart = ref(null);
const distributionChart = ref(null);
const costChart = ref(null);
const selectedAccountId = ref("all");
let trendInstance = null;
let distributionInstance = null;
let costInstance = null;
let resizeObserver = null;
let resizeFrame = 0;
const accountOptions = computed(() => props.payload?.accounts ?? []);
const selectedAccount = computed(() => accountOptions.value.find((item) => item.id === selectedAccountId.value) ?? null);
const effectiveRecords = computed(() => selectedAccount.value?.records ?? props.payload?.records ?? []);
const hasUsageData = computed(() => effectiveRecords.value.length > 0);
const showEmptyPanel = computed(() => props.statusKind !== "error" && (!props.payload || !hasUsageData.value));
const resolvedEmptyDescription = computed(() => {
    if (!props.payload)
        return props.emptyDescription;
    return buildEmptyDescription(props.payload, props.emptyDescription);
});
const syncMetaLabel = computed(() => {
    if (!props.payload)
        return "等待数据";
    if (hasUsageData.value)
        return `${props.payload.generatedAt} 已同步`;
    return `${props.payload.generatedAt} 扫描完成（无用量）`;
});
const allAccountsView = computed(() => props.payload?.views?.[props.activeRange] ?? props.payload?.views?.history ?? null);
const currentView = computed(() => {
    const views = selectedAccount.value?.views ?? props.payload?.views;
    return views?.[props.activeRange] ?? views?.history ?? null;
});
const chartView = computed(() => {
    if (!currentView.value)
        return null;
    if (props.activeRange !== "history" || !props.payload)
        return currentView.value;
    return {
        ...currentView.value,
        ...buildHistoryCharts(effectiveRecords.value, currentView.value.range.start, currentView.value.range.end, props.chartWidth)
    };
});
onBeforeUnmount(() => {
    resizeObserver?.disconnect();
    resizeObserver = null;
    if (resizeFrame)
        cancelAnimationFrame(resizeFrame);
    trendInstance?.dispose();
    distributionInstance?.dispose();
    costInstance?.dispose();
});
async function renderCharts() {
    if (!props.active)
        return;
    await nextTick();
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    if (!chartView.value)
        return;
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
    if (!props.active || !chartView.value)
        return;
    void renderCharts();
}
watch(chartView, tryRenderCharts, { immediate: true });
watch(() => props.active, tryRenderCharts, { immediate: true });
watch(() => props.payload?.activeAccountId, () => {
    if (selectedAccountId.value !== "all" && !accountOptions.value.some((item) => item.id === selectedAccountId.value)) {
        selectedAccountId.value = "all";
    }
});
watch(() => props.chartWidth, () => {
    if (props.active)
        scheduleChartResize();
});
onMounted(() => {
    resizeObserver = new ResizeObserver(scheduleChartResize);
    [trendChart.value, distributionChart.value, costChart.value].forEach((element) => {
        if (element)
            resizeObserver?.observe(element);
    });
    tryRenderCharts();
});
function ensureChartInstance(element, instance) {
    if (instance && instance.getDom() === element)
        return instance;
    instance?.dispose();
    return echarts.init(element);
}
function scheduleChartResize() {
    if (!props.active)
        return;
    if (resizeFrame)
        cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        trendInstance?.resize();
        distributionInstance?.resize();
        costInstance?.resize();
    });
}
function exportCsv() {
    if (!props.payload || !currentView.value)
        return;
    const rows = effectiveRecords.value.filter((row) => row[0] >= currentView.value.range.start && row[0] <= currentView.value.range.end);
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
function priceRecord(row) {
    if (typeof row[8] === "number" && Number.isFinite(row[8]))
        return row[8];
    const rules = props.payload?.pricingRules ?? [];
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
function buildHistoryCharts(records, start, end, width) {
    const rows = records.filter((row) => row[0] >= start && row[0] <= end);
    const granularity = chooseHistoryGranularity(start, end, targetBucketCount(width));
    const bucketMap = new Map();
    for (const row of rows) {
        const ts = bucketStart(row[0], granularity);
        const bucket = bucketMap.get(ts) ??
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
        trend: buckets.map((bucket) => [bucket.trend[0], bucket.trend[1], bucket.trend[2], bucket.trend[3], bucket.trend[4], bucket.trend[5], bucket.trend[6], Number(bucket.trend[7].toFixed(6))]),
        distribution: buckets.map((bucket) => [bucket.distribution[0], bucket.distribution[1], bucket.distribution[2], Number(bucket.distribution[3].toFixed(6))])
    };
}
function targetBucketCount(width) {
    const safeWidth = width > 0 ? width : 900;
    return Math.max(36, Math.min(180, Math.round(safeWidth / 10)));
}
function chooseHistoryGranularity(start, end, targetBuckets) {
    const duration = Math.max(1, end - start);
    if (Math.ceil(duration / 3_600_000) <= targetBuckets)
        return "hour";
    if (Math.ceil(duration / 86_400_000) <= targetBuckets)
        return "day";
    if (monthSpan(start, end) <= targetBuckets)
        return "month";
    return "year";
}
function bucketStart(ts, granularity) {
    const date = new Date(ts);
    if (granularity === "year")
        return new Date(date.getFullYear(), 0, 1).getTime();
    if (granularity === "month")
        return new Date(date.getFullYear(), date.getMonth(), 1).getTime();
    if (granularity === "day")
        return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    if (granularity === "hour")
        return new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()).getTime();
    return new Date(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours(), date.getMinutes()).getTime();
}
function monthSpan(start, end) {
    const startDate = new Date(start);
    const endDate = new Date(end);
    return Math.max(1, (endDate.getFullYear() - startDate.getFullYear()) * 12 + endDate.getMonth() - startDate.getMonth() + 1);
}
function csvCell(value) {
    const text = String(value ?? "");
    return /[",\r\n]/.test(text) ? `"${text.replaceAll("\"", "\"\"")}"` : text;
}
const __VLS_exposed = { scheduleChartResize };
defineExpose(__VLS_exposed);
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
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
                __VLS_ctx.$emit('update:activeRange', range.id);
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
(__VLS_ctx.syncMetaLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.exportCsv) },
    ...{ class: "text-button" },
    disabled: (!__VLS_ctx.currentView),
});
const __VLS_0 = {}.Download;
/** @type {[typeof __VLS_components.Download, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    size: (16),
}));
const __VLS_2 = __VLS_1({
    size: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
if (__VLS_ctx.accountOptions.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "account-section" },
        'aria-label': "Cursor 账号用量",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.accountOptions.length))
                    return;
                __VLS_ctx.selectedAccountId = 'all';
            } },
        ...{ class: "account-card" },
        ...{ class: ({ active: __VLS_ctx.selectedAccountId === 'all' }) },
        type: "button",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "account-name" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
    (__VLS_ctx.allAccountsView?.summary.totalTokensLabel ?? "0");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
    (__VLS_ctx.allAccountsView?.summary.requestsLabel ?? "0");
    ((__VLS_ctx.allAccountsView?.cost.total ?? 0).toFixed(2));
    for (const [account] of __VLS_getVForSourceType((__VLS_ctx.accountOptions))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.accountOptions.length))
                        return;
                    __VLS_ctx.selectedAccountId = account.id;
                } },
            key: (account.id),
            ...{ class: "account-card" },
            ...{ class: ({ active: __VLS_ctx.selectedAccountId === account.id }) },
            type: "button",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "account-name" },
        });
        (account.label);
        if (account.isCurrent) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.strong, __VLS_intrinsicElements.strong)({});
        (account.views[__VLS_ctx.activeRange]?.summary.totalTokensLabel ?? "0");
        __VLS_asFunctionalElement(__VLS_intrinsicElements.small, __VLS_intrinsicElements.small)({});
        (account.idSuffix);
        (account.views[__VLS_ctx.activeRange]?.summary.requestsLabel ?? "0");
        if (account.isOnline && account.syncStatus !== 'ok') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.em, __VLS_intrinsicElements.em)({});
        }
    }
}
if (__VLS_ctx.showEmptyPanel) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-panel" },
    });
    const __VLS_4 = {}.Database;
    /** @type {[typeof __VLS_components.Database, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        size: (34),
    }));
    const __VLS_6 = __VLS_5({
        size: (34),
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
    (__VLS_ctx.emptyTitle);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    (__VLS_ctx.resolvedEmptyDescription);
}
else if (__VLS_ctx.statusKind === 'error') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-panel error" },
    });
    const __VLS_8 = {}.TriangleAlert;
    /** @type {[typeof __VLS_components.TriangleAlert, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        size: (34),
    }));
    const __VLS_10 = __VLS_9({
        size: (34),
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    (__VLS_ctx.statusMessage);
}
else if (__VLS_ctx.currentView && __VLS_ctx.hasUsageData) {
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
    (__VLS_ctx.riskCaption);
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
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['range-row']} */ ;
/** @type {__VLS_StyleScopedClasses['range-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['sync-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['text-button']} */ ;
/** @type {__VLS_StyleScopedClasses['account-section']} */ ;
/** @type {__VLS_StyleScopedClasses['account-card']} */ ;
/** @type {__VLS_StyleScopedClasses['account-name']} */ ;
/** @type {__VLS_StyleScopedClasses['account-card']} */ ;
/** @type {__VLS_StyleScopedClasses['account-name']} */ ;
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
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            Database: Database,
            Download: Download,
            TriangleAlert: TriangleAlert,
            ranges: ranges,
            trendChart: trendChart,
            distributionChart: distributionChart,
            costChart: costChart,
            selectedAccountId: selectedAccountId,
            accountOptions: accountOptions,
            hasUsageData: hasUsageData,
            showEmptyPanel: showEmptyPanel,
            resolvedEmptyDescription: resolvedEmptyDescription,
            syncMetaLabel: syncMetaLabel,
            allAccountsView: allAccountsView,
            currentView: currentView,
            exportCsv: exportCsv,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {
            ...__VLS_exposed,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
//# sourceMappingURL=AgentDashboard.vue.js.map