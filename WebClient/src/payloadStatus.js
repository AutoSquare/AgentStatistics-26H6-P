export function hasUsageRecords(payload) {
    return (payload?.records?.length ?? 0) > 0;
}
export function resolveSyncResult(payload) {
    return payload?.sync ?? payload?.limits?.sync;
}
export function buildAgentStatusMessage(payload) {
    const recordCount = payload.records.length;
    if (recordCount > 0) {
        return `已同步 ${recordCount} 条用量记录`;
    }
    const sync = resolveSyncResult(payload);
    if (sync?.error)
        return sync.error;
    if (payload.dataStatus === "parse_empty") {
        return payload.source === "antigravity"
            ? "已同步 Antigravity 缓存，但未解析到有效用量记录"
            : "CSV 已同步，但未解析到有效用量行";
    }
    if (payload.source === "codex") {
        return "暂无 Codex 用量，请检查 sessions 路径";
    }
    if (payload.source === "cursor") {
        return "暂无 Cursor 用量，请配置 tokscale 凭证或本机 Cursor 登录缓存后刷新";
    }
    if (payload.source === "antigravity") {
        return "暂无 Antigravity 用量，请运行 agy CLI 对话或保留 CLI 会话时刷新";
    }
    return "暂无可用用量数据";
}
export function buildEmptyDescription(payload, fallback) {
    const sync = resolveSyncResult(payload);
    if (sync?.error)
        return sync.error;
    if (payload.dataStatus === "parse_empty") {
        return "云端 CSV 已下载，但未解析到有效用量行。请确认 Cursor 账号近期是否有 AI 调用。";
    }
    if (payload.dataStatus === "sync_failed" && payload.source === "cursor") {
        return "同步失败。请配置 tokscale / token-monitor 凭证，或使用本机 Cursor 登录缓存（无需保持应用打开）。";
    }
    if (payload.dataStatus === "sync_failed" && payload.source === "antigravity") {
        return "同步失败。请运行 Antigravity CLI（agy）并保持会话，或确认本地 antigravity-cache / transcript 已有数据。";
    }
    return fallback;
}
//# sourceMappingURL=payloadStatus.js.map