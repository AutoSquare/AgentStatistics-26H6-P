export function hasUsageRecords(payload) {
    return (payload?.records?.length ?? 0) > 0;
}
export function resolveSyncResult(payload) {
    return payload?.sync ?? payload?.limits?.sync;
}
export function buildAgentStatusMessage(payload) {
    const recordCount = payload.records.length;
    if (recordCount > 0) {
        if (payload.source === "cursor")
            return "已同步 Cursor 用量。";
        if (payload.source === "codex")
            return "已同步 Codex 用量。";
        if (payload.source === "antigravity")
            return "已同步 Antigravity 用量。";
        return `已同步 ${recordCount} 条用量记录`;
    }
    const sync = resolveSyncResult(payload);
    if (sync?.error)
        return sync.error;
    if (payload.dataStatus === "parse_empty") {
        if (payload.source === "antigravity")
            return "暂无 Antigravity 用量。";
        if (payload.source === "cursor")
            return "暂无 Cursor 用量。";
        return "暂无 Codex 用量。";
    }
    if (payload.source === "codex") {
        return "暂无 Codex 用量。";
    }
    if (payload.source === "cursor") {
        return "暂无 Cursor 用量。";
    }
    if (payload.source === "antigravity") {
        return "暂无 Antigravity 用量。";
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
        return "Cursor 同步失败。";
    }
    if (payload.dataStatus === "sync_failed" && payload.source === "antigravity") {
        return "Antigravity 同步失败。";
    }
    return fallback;
}
//# sourceMappingURL=payloadStatus.js.map