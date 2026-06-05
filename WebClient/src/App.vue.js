import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Activity, FileText, FolderInput, Layers3, MousePointer2, Orbit, RefreshCw } from "@lucide/vue";
import AgentDashboard from "./components/AgentDashboard.vue";
import { onHostMessage, postToHost } from "./host";
import { buildAgentStatusMessage } from "./payloadStatus";
const navItems = [
    { id: "codex", label: "Codex", icon: Activity },
    { id: "cursor", label: "Cursor", icon: MousePointer2 },
    { id: "antigravity", label: "Antigravity", icon: Orbit },
    { id: "generic", label: "通用", icon: FileText },
    { id: "total", label: "总计", icon: Layers3 }
];
const activePage = ref("codex");
const statusKind = ref("idle");
const statusMessage = ref("等待宿主连接");
const pageStatuses = ref({ codex: "idle", cursor: "idle", antigravity: "idle" });
const pageMessages = ref({ codex: "等待数据", cursor: "等待数据", antigravity: "等待数据" });
const codexRootDraft = ref("");
const cursorCacheDraft = ref("");
const antigravityCacheDraft = ref("");
const cursorAuthAvailable = ref(false);
const codexData = ref(null);
const cursorData = ref(null);
const antigravityData = ref(null);
const codexRange = ref("today");
const cursorRange = ref("today");
const antigravityRange = ref("today");
const sidebarWidth = ref(loadSidebarWidth());
const workspace = ref(null);
const codexDashboard = ref(null);
const cursorDashboard = ref(null);
const antigravityDashboard = ref(null);
const chartWidth = ref(0);
let resizeObserver = null;
let resizeFrame = 0;
const pageTitle = computed(() => {
    if (activePage.value === "codex")
        return "Codex 用量统计";
    if (activePage.value === "cursor")
        return "Cursor 用量统计";
    if (activePage.value === "antigravity")
        return "Antigravity 用量统计";
    if (activePage.value === "generic")
        return "通用文件分析";
    return "跨 Agent 总计";
});
function pageStatus(source) {
    return pageStatuses.value[source];
}
function pageMessage(source) {
    return pageMessages.value[source];
}
onMounted(() => {
    onHostMessage((message) => {
        if (message.type === "settings") {
            if (typeof message.codexRoot === "string")
                codexRootDraft.value = message.codexRoot;
            if (typeof message.cursorCachePath === "string")
                cursorCacheDraft.value = message.cursorCachePath;
            if (typeof message.antigravityCachePath === "string")
                antigravityCacheDraft.value = message.antigravityCachePath;
            if (typeof message.cursorAuthAvailable === "boolean")
                cursorAuthAvailable.value = message.cursorAuthAvailable;
        }
        if (message.type === "status") {
            const source = typeof message.source === "string" ? message.source : null;
            const status = message.status ?? "idle";
            const text = typeof message.message === "string" ? message.message : "";
            if (source && (source === "codex" || source === "cursor" || source === "antigravity")) {
                pageStatuses.value[source] = status;
                pageMessages.value[source] = text;
            }
            statusKind.value = status;
            statusMessage.value = text;
        }
        if (message.type === "codexData") {
            const payload = message.payload;
            codexData.value = payload;
            pageStatuses.value.codex = "idle";
            pageMessages.value.codex = buildAgentStatusMessage(payload);
        }
        if (message.type === "cursorData") {
            const payload = message.payload;
            cursorData.value = payload;
            pageStatuses.value.cursor = "idle";
            pageMessages.value.cursor = buildAgentStatusMessage(payload);
        }
        if (message.type === "antigravityData") {
            const payload = message.payload;
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
    if (workspace.value)
        resizeObserver.observe(workspace.value);
    scheduleChartResize();
});
onBeforeUnmount(() => {
    window.removeEventListener("resize", scheduleChartResize);
    resizeObserver?.disconnect();
    resizeObserver = null;
    if (resizeFrame)
        cancelAnimationFrame(resizeFrame);
    stopResize();
});
watch(activePage, () => {
    scheduleChartResize();
});
function scheduleChartResize() {
    if (resizeFrame)
        cancelAnimationFrame(resizeFrame);
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
function refreshAntigravity() {
    postToHost({ type: "refreshAntigravity" });
}
function saveCodexRoot() {
    postToHost({ type: "setCodexRoot", path: codexRootDraft.value });
}
function saveCursorCachePath() {
    postToHost({ type: "setCursorCachePath", path: cursorCacheDraft.value });
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
    scheduleChartResize();
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
    scheduleChartResize();
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
    ref: "workspace",
    ...{ class: "workspace" },
    tabindex: "-1",
});
/** @type {typeof __VLS_ctx.workspace} */ ;
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
        ...{ onClick: (__VLS_ctx.refreshCodex) },
        ...{ class: "primary-button" },
        disabled: (__VLS_ctx.pageStatus('codex') === 'scanning'),
    });
    const __VLS_8 = {}.RefreshCw;
    /** @type {[typeof __VLS_components.RefreshCw, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('codex') === 'scanning' }) },
    }));
    const __VLS_10 = __VLS_9({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('codex') === 'scanning' }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
}
else if (__VLS_ctx.activePage === 'cursor') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "topbar-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "path-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onKeyup: (__VLS_ctx.saveCursorCachePath) },
        value: (__VLS_ctx.cursorCacheDraft),
        type: "text",
        spellcheck: "false",
        title: "cursor-cache 目录完整路径",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "auth-hint" },
        ...{ class: ({ ready: __VLS_ctx.cursorAuthAvailable }) },
    });
    (__VLS_ctx.cursorAuthAvailable ? "将自动读取 tokscale 凭证或本机 Cursor 登录缓存（无需保持应用打开）" : "未检测到 Cursor 登录态，请配置 tokscale 凭证或在本机 Cursor 完成一次登录");
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.saveCursorCachePath) },
        ...{ class: "secondary-button" },
    });
    const __VLS_12 = {}.FolderInput;
    /** @type {[typeof __VLS_components.FolderInput, ]} */ ;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        size: (17),
    }));
    const __VLS_14 = __VLS_13({
        size: (17),
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.refreshCursor) },
        ...{ class: "primary-button" },
        disabled: (__VLS_ctx.pageStatus('cursor') === 'scanning'),
    });
    const __VLS_16 = {}.RefreshCw;
    /** @type {[typeof __VLS_components.RefreshCw, ]} */ ;
    // @ts-ignore
    const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('cursor') === 'scanning' }) },
    }));
    const __VLS_18 = __VLS_17({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('cursor') === 'scanning' }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_17));
}
else if (__VLS_ctx.activePage === 'antigravity') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "topbar-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "path-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onKeyup: (__VLS_ctx.saveAntigravityCachePath) },
        value: (__VLS_ctx.antigravityCacheDraft),
        type: "text",
        spellcheck: "false",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.saveAntigravityCachePath) },
        ...{ class: "secondary-button" },
    });
    const __VLS_20 = {}.FolderInput;
    /** @type {[typeof __VLS_components.FolderInput, ]} */ ;
    // @ts-ignore
    const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
        size: (17),
    }));
    const __VLS_22 = __VLS_21({
        size: (17),
    }, ...__VLS_functionalComponentArgsRest(__VLS_21));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.refreshAntigravity) },
        ...{ class: "primary-button" },
        disabled: (__VLS_ctx.pageStatus('antigravity') === 'scanning'),
    });
    const __VLS_24 = {}.RefreshCw;
    /** @type {[typeof __VLS_components.RefreshCw, ]} */ ;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent(__VLS_24, new __VLS_24({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('antigravity') === 'scanning' }) },
    }));
    const __VLS_26 = __VLS_25({
        size: (17),
        ...{ class: ({ spinning: __VLS_ctx.pageStatus('antigravity') === 'scanning' }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_25));
}
if (__VLS_ctx.activePage === 'codex') {
    /** @type {[typeof AgentDashboard, ]} */ ;
    // @ts-ignore
    const __VLS_28 = __VLS_asFunctionalComponent(AgentDashboard, new AgentDashboard({
        ...{ 'onUpdate:activeRange': {} },
        ref: "codexDashboard",
        payload: (__VLS_ctx.codexData),
        activeRange: (__VLS_ctx.codexRange),
        statusKind: (__VLS_ctx.pageStatus('codex')),
        statusMessage: (__VLS_ctx.pageMessage('codex')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'codex'),
        emptyTitle: "等待 Codex 用量数据",
        emptyDescription: "应用会监听本地 Codex 会话日志。也可以点击刷新立即扫描。",
        riskCaption: "来自 Codex rate_limits",
    }));
    const __VLS_29 = __VLS_28({
        ...{ 'onUpdate:activeRange': {} },
        ref: "codexDashboard",
        payload: (__VLS_ctx.codexData),
        activeRange: (__VLS_ctx.codexRange),
        statusKind: (__VLS_ctx.pageStatus('codex')),
        statusMessage: (__VLS_ctx.pageMessage('codex')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'codex'),
        emptyTitle: "等待 Codex 用量数据",
        emptyDescription: "应用会监听本地 Codex 会话日志。也可以点击刷新立即扫描。",
        riskCaption: "来自 Codex rate_limits",
    }, ...__VLS_functionalComponentArgsRest(__VLS_28));
    let __VLS_31;
    let __VLS_32;
    let __VLS_33;
    const __VLS_34 = {
        'onUpdate:activeRange': (...[$event]) => {
            if (!(__VLS_ctx.activePage === 'codex'))
                return;
            __VLS_ctx.codexRange = $event;
        }
    };
    /** @type {typeof __VLS_ctx.codexDashboard} */ ;
    var __VLS_35 = {};
    var __VLS_30;
}
else if (__VLS_ctx.activePage === 'cursor') {
    /** @type {[typeof AgentDashboard, ]} */ ;
    // @ts-ignore
    const __VLS_37 = __VLS_asFunctionalComponent(AgentDashboard, new AgentDashboard({
        ...{ 'onUpdate:activeRange': {} },
        ref: "cursorDashboard",
        payload: (__VLS_ctx.cursorData),
        activeRange: (__VLS_ctx.cursorRange),
        statusKind: (__VLS_ctx.pageStatus('cursor')),
        statusMessage: (__VLS_ctx.pageMessage('cursor')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'cursor'),
        emptyTitle: "等待 Cursor 用量数据",
        emptyDescription: "应用会读取 tokscale / token-monitor 凭证或本机 Cursor 登录缓存并同步云端用量，无需保持 Cursor 应用打开。",
        riskCaption: "来自 Cursor usage-summary API",
    }));
    const __VLS_38 = __VLS_37({
        ...{ 'onUpdate:activeRange': {} },
        ref: "cursorDashboard",
        payload: (__VLS_ctx.cursorData),
        activeRange: (__VLS_ctx.cursorRange),
        statusKind: (__VLS_ctx.pageStatus('cursor')),
        statusMessage: (__VLS_ctx.pageMessage('cursor')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'cursor'),
        emptyTitle: "等待 Cursor 用量数据",
        emptyDescription: "应用会读取 tokscale / token-monitor 凭证或本机 Cursor 登录缓存并同步云端用量，无需保持 Cursor 应用打开。",
        riskCaption: "来自 Cursor usage-summary API",
    }, ...__VLS_functionalComponentArgsRest(__VLS_37));
    let __VLS_40;
    let __VLS_41;
    let __VLS_42;
    const __VLS_43 = {
        'onUpdate:activeRange': (...[$event]) => {
            if (!!(__VLS_ctx.activePage === 'codex'))
                return;
            if (!(__VLS_ctx.activePage === 'cursor'))
                return;
            __VLS_ctx.cursorRange = $event;
        }
    };
    /** @type {typeof __VLS_ctx.cursorDashboard} */ ;
    var __VLS_44 = {};
    var __VLS_39;
}
else if (__VLS_ctx.activePage === 'antigravity') {
    /** @type {[typeof AgentDashboard, ]} */ ;
    // @ts-ignore
    const __VLS_46 = __VLS_asFunctionalComponent(AgentDashboard, new AgentDashboard({
        ...{ 'onUpdate:activeRange': {} },
        ref: "antigravityDashboard",
        payload: (__VLS_ctx.antigravityData),
        activeRange: (__VLS_ctx.antigravityRange),
        statusKind: (__VLS_ctx.pageStatus('antigravity')),
        statusMessage: (__VLS_ctx.pageMessage('antigravity')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'antigravity'),
        emptyTitle: "等待 Antigravity 用量数据",
        emptyDescription: "刷新时会从运行中的 Antigravity CLI（agy）同步用量，并读取 ~/.gemini/antigravity-cli 的 transcript 与 antigravity-cache；CLI 未运行时仍可读取已有本地数据。",
        riskCaption: "来自 Antigravity Connect RPC",
    }));
    const __VLS_47 = __VLS_46({
        ...{ 'onUpdate:activeRange': {} },
        ref: "antigravityDashboard",
        payload: (__VLS_ctx.antigravityData),
        activeRange: (__VLS_ctx.antigravityRange),
        statusKind: (__VLS_ctx.pageStatus('antigravity')),
        statusMessage: (__VLS_ctx.pageMessage('antigravity')),
        chartWidth: (__VLS_ctx.chartWidth),
        active: (__VLS_ctx.activePage === 'antigravity'),
        emptyTitle: "等待 Antigravity 用量数据",
        emptyDescription: "刷新时会从运行中的 Antigravity CLI（agy）同步用量，并读取 ~/.gemini/antigravity-cli 的 transcript 与 antigravity-cache；CLI 未运行时仍可读取已有本地数据。",
        riskCaption: "来自 Antigravity Connect RPC",
    }, ...__VLS_functionalComponentArgsRest(__VLS_46));
    let __VLS_49;
    let __VLS_50;
    let __VLS_51;
    const __VLS_52 = {
        'onUpdate:activeRange': (...[$event]) => {
            if (!!(__VLS_ctx.activePage === 'codex'))
                return;
            if (!!(__VLS_ctx.activePage === 'cursor'))
                return;
            if (!(__VLS_ctx.activePage === 'antigravity'))
                return;
            __VLS_ctx.antigravityRange = $event;
        }
    };
    /** @type {typeof __VLS_ctx.antigravityDashboard} */ ;
    var __VLS_53 = {};
    var __VLS_48;
}
else if (__VLS_ctx.activePage === 'generic') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "page" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-panel" },
    });
    const __VLS_55 = {}.FileText;
    /** @type {[typeof __VLS_components.FileText, ]} */ ;
    // @ts-ignore
    const __VLS_56 = __VLS_asFunctionalComponent(__VLS_55, new __VLS_55({
        size: (36),
    }));
    const __VLS_57 = __VLS_56({
        size: (36),
    }, ...__VLS_functionalComponentArgsRest(__VLS_56));
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
    const __VLS_59 = {}.Layers3;
    /** @type {[typeof __VLS_components.Layers3, ]} */ ;
    // @ts-ignore
    const __VLS_60 = __VLS_asFunctionalComponent(__VLS_59, new __VLS_59({
        size: (36),
    }));
    const __VLS_61 = __VLS_60({
        size: (36),
    }, ...__VLS_functionalComponentArgsRest(__VLS_60));
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
/** @type {__VLS_StyleScopedClasses['topbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['path-field']} */ ;
/** @type {__VLS_StyleScopedClasses['auth-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['secondary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['primary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['topbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['path-field']} */ ;
/** @type {__VLS_StyleScopedClasses['secondary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['primary-button']} */ ;
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['page']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-panel']} */ ;
// @ts-ignore
var __VLS_36 = __VLS_35, __VLS_45 = __VLS_44, __VLS_54 = __VLS_53;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            FileText: FileText,
            FolderInput: FolderInput,
            Layers3: Layers3,
            RefreshCw: RefreshCw,
            AgentDashboard: AgentDashboard,
            navItems: navItems,
            activePage: activePage,
            statusKind: statusKind,
            statusMessage: statusMessage,
            codexRootDraft: codexRootDraft,
            cursorCacheDraft: cursorCacheDraft,
            antigravityCacheDraft: antigravityCacheDraft,
            cursorAuthAvailable: cursorAuthAvailable,
            codexData: codexData,
            cursorData: cursorData,
            antigravityData: antigravityData,
            codexRange: codexRange,
            cursorRange: cursorRange,
            antigravityRange: antigravityRange,
            sidebarWidth: sidebarWidth,
            workspace: workspace,
            codexDashboard: codexDashboard,
            cursorDashboard: cursorDashboard,
            antigravityDashboard: antigravityDashboard,
            chartWidth: chartWidth,
            pageTitle: pageTitle,
            pageStatus: pageStatus,
            pageMessage: pageMessage,
            refreshCodex: refreshCodex,
            refreshCursor: refreshCursor,
            refreshAntigravity: refreshAntigravity,
            saveCodexRoot: saveCodexRoot,
            saveCursorCachePath: saveCursorCachePath,
            saveAntigravityCachePath: saveAntigravityCachePath,
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