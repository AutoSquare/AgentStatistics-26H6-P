import type { EChartsOption } from "echarts";
import type { CodexView } from "./types";

export type TimeGranularity = "minute" | "hour" | "day" | "month" | "year";

export interface ChartView extends CodexView {
  axisGranularity?: TimeGranularity;
}

const axisLabel = { color: "#64748b", fontSize: 11 };
const valueAxisLabel = { ...axisLabel, formatter: formatCompactNumber };
const grid = { top: 28, right: 18, bottom: 34, left: 8, containLabel: true };

export function trendOption(view: ChartView): EChartsOption {
  const rows = view.trend ?? [];
  return {
    color: ["#1e40af", "#0f766e", "#0284c7", "#d97706", "#7c3aed"],
    tooltip: { trigger: "axis", valueFormatter: (value) => Number(value).toLocaleString() },
    legend: { top: 0, right: 0, textStyle: { color: "#475569" } },
    grid,
    xAxis: { type: "category", data: rows.map((row) => formatTime(row[0], view.axisGranularity)), axisLabel },
    yAxis: { type: "value", axisLabel: valueAxisLabel },
    series: [
      series("总量", rows.map((row) => row[1])),
      series("缓存", rows.map((row) => row[2])),
      series("输出", rows.map((row) => row[3])),
      series("输入", rows.map((row) => row[4])),
      series("推理", rows.map((row) => row[5]))
    ]
  };
}

export function distributionOption(view: ChartView): EChartsOption {
  const rows = view.distribution ?? [];
  return {
    color: ["#3b82f6", "#d97706"],
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 0, textStyle: { color: "#475569" } },
    grid,
    xAxis: { type: "category", data: rows.map((row) => formatTime(row[0], view.axisGranularity)), axisLabel },
    yAxis: [
      { type: "value", axisLabel: valueAxisLabel },
      { type: "value", axisLabel: valueAxisLabel }
    ],
    series: [
      { name: "Token", type: "bar", data: rows.map((row) => row[1]), barMaxWidth: 18, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      { name: "调用", type: "line", yAxisIndex: 1, smooth: true, data: rows.map((row) => row[2]), symbolSize: 4 }
    ]
  };
}

export function costOption(view: CodexView): EChartsOption {
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

function series(name: string, data: number[]) {
  return {
    name,
    type: "line" as const,
    smooth: true,
    data,
    symbolSize: 3,
    lineStyle: { width: name === "总量" ? 3 : 2 },
    areaStyle: name === "总量" ? { opacity: 0.08 } : undefined
  };
}

function formatTime(ts: number, granularity: TimeGranularity = "minute") {
  const date = new Date(ts);
  const yyyy = date.getFullYear().toString();
  const mm = (date.getMonth() + 1).toString().padStart(2, "0");
  const dd = date.getDate().toString().padStart(2, "0");
  const hh = date.getHours().toString().padStart(2, "0");
  const min = date.getMinutes().toString().padStart(2, "0");
  if (granularity === "year") return yyyy;
  if (granularity === "month") return `${yyyy}-${mm}`;
  if (granularity === "day") return `${mm}-${dd}`;
  if (granularity === "hour") return `${mm}-${dd} ${hh}:00`;
  return `${hh}:${min}`;
}

function formatCompactNumber(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const abs = Math.abs(number);
  if (abs >= 100_000_000) return `${trimNumber(number / 100_000_000)}亿`;
  if (abs >= 10_000) return `${trimNumber(number / 10_000)}万`;
  return Math.round(number).toLocaleString();
}

function trimNumber(value: number) {
  return value.toFixed(value >= 10 ? 0 : 1).replace(/\.0$/, "");
}
