const axisLabel = { color: "#64748b", fontSize: 11 };
const grid = { top: 26, right: 18, bottom: 28, left: 52 };
export function trendOption(view) {
    const rows = view.trend ?? [];
    return {
        color: ["#1e40af", "#0f766e", "#0284c7", "#d97706", "#7c3aed"],
        tooltip: { trigger: "axis", valueFormatter: (value) => Number(value).toLocaleString() },
        legend: { top: 0, right: 0, textStyle: { color: "#475569" } },
        grid,
        xAxis: { type: "category", data: rows.map((row) => formatTime(row[0])), axisLabel },
        yAxis: { type: "value", axisLabel },
        series: [
            series("总量", rows.map((row) => row[1])),
            series("缓存", rows.map((row) => row[2])),
            series("输出", rows.map((row) => row[3])),
            series("输入", rows.map((row) => row[4])),
            series("推理", rows.map((row) => row[5]))
        ]
    };
}
export function distributionOption(view) {
    const rows = view.distribution ?? [];
    return {
        color: ["#3b82f6", "#d97706"],
        tooltip: { trigger: "axis" },
        legend: { top: 0, right: 0, textStyle: { color: "#475569" } },
        grid,
        xAxis: { type: "category", data: rows.map((row) => formatTime(row[0])), axisLabel },
        yAxis: [
            { type: "value", axisLabel },
            { type: "value", axisLabel }
        ],
        series: [
            { name: "Token", type: "bar", data: rows.map((row) => row[1]), barMaxWidth: 18, itemStyle: { borderRadius: [4, 4, 0, 0] } },
            { name: "调用", type: "line", yAxisIndex: 1, smooth: true, data: rows.map((row) => row[2]), symbolSize: 4 }
        ]
    };
}
export function costOption(view) {
    const parts = view.cost?.parts ?? [];
    return {
        color: ["#1e40af", "#0f766e", "#0284c7", "#7c3aed"],
        tooltip: { trigger: "item", valueFormatter: (value) => `$${Number(value).toFixed(2)}` },
        series: [
            {
                type: "pie",
                radius: ["54%", "76%"],
                center: ["50%", "52%"],
                label: { color: "#334155", formatter: "{b}\n{d}%" },
                data: parts.map((part) => ({ name: part.name, value: part.value }))
            }
        ]
    };
}
function series(name, data) {
    return {
        name,
        type: "line",
        smooth: true,
        data,
        symbolSize: 3,
        lineStyle: { width: name === "总量" ? 3 : 2 },
        areaStyle: name === "总量" ? { opacity: 0.08 } : undefined
    };
}
function formatTime(ts) {
    const date = new Date(ts);
    return `${date.getHours().toString().padStart(2, "0")}:${date.getMinutes().toString().padStart(2, "0")}`;
}
//# sourceMappingURL=charts.js.map