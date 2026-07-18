import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import type { BacktestOrder, BacktestSeries } from "./types";


echarts.use([
  BarChart,
  CandlestickChart,
  LineChart,
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
]);

function signalTime(signal: Record<string, unknown>) {
  const rightShoulder = signal.right_shoulder as Record<string, unknown> | undefined;
  return String(signal.retest_time || signal.break_time || rightShoulder?.time || "");
}

function signalPoint(signal: Record<string, unknown>, key: string) {
  const value = signal[key];
  if (!value || typeof value !== "object") return null;
  const point = value as Record<string, unknown>;
  const time = typeof point.time === "string" ? point.time : "";
  const price = Number(point.price);
  return time && Number.isFinite(price) ? { time, price } : null;
}

const STRUCTURE_POINTS = [
  ["left_shoulder", "左肩"],
  ["left_neck", "左颈"],
  ["head", "头部"],
  ["right_neck", "右颈"],
  ["right_shoulder", "右肩"],
] as const;

export function BacktestChart({ series, order }: { series: BacktestSeries; order: BacktestOrder | null }) {
  const elementRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!elementRef.current) return;
    const chart = echarts.init(elementRef.current, undefined, { renderer: "canvas" });
    const candles = series.chart.candles;
    const times = candles.map((item) => item.display_time || item.time);
    const rawTimes = candles.map((item) => item.time);
    const candleValues = candles.map((item) => [item.open, item.close, item.low, item.high]);
    const ma5 = candles.map((item) => item.ma?.ma5 ?? null);
    const ma10 = candles.map((item) => item.ma?.ma10 ?? null);
    const ma20 = candles.map((item) => item.ma?.ma20 ?? null);
    const ma30 = candles.map((item) => item.ma?.ma30 ?? null);
    const ma60 = candles.map((item) => item.ma?.ma60 ?? null);
    const selectedSignal = order?.signal || null;
    const structureMarkPoints: Array<Record<string, unknown>> = selectedSignal ? STRUCTURE_POINTS.map(([key, label]) => {
      const point = signalPoint(selectedSignal, key);
      const index = point ? rawTimes.indexOf(point.time) : -1;
      return index >= 0 && point ? { coord: [index, point.price], name: label, value: label, itemStyle: { color: "#6f4a46" } } : null;
    }).filter((item): item is NonNullable<typeof item> => Boolean(item)) : [];
    const allSignalMarkPoints: Array<Record<string, unknown>> = series.signals.map((signal) => {
      const time = signalTime(signal);
      const index = rawTimes.indexOf(time);
      const pattern = signal.pattern === "head_shoulders_top" ? "顶" : "底";
      return index >= 0 ? { coord: [index, candles[index].close], name: pattern, value: pattern } : null;
    }).filter((item): item is NonNullable<typeof item> => Boolean(item));
    const markPoints = selectedSignal ? structureMarkPoints : allSignalMarkPoints;
    if (order?.entry_time) {
      const index = rawTimes.indexOf(order.entry_time);
      if (index >= 0) markPoints.push({ coord: [index, Number(order.entry_price)], name: "进", value: "进", itemStyle: { color: "#1168a8" } });
    }
    if (order?.exit_time) {
      const index = rawTimes.indexOf(order.exit_time);
      if (index >= 0) markPoints.push({ coord: [index, Number(order.exit_price)], name: "出", value: "出", itemStyle: { color: order.exit_reason === "TAKE_PROFIT" ? "#b33a3a" : "#16805b" } });
    }
    const selectedNecklineSeries = selectedSignal ? (() => {
      const leftNeck = signalPoint(selectedSignal, "left_neck");
      const rightNeck = signalPoint(selectedSignal, "right_neck");
      const start = leftNeck ? rawTimes.indexOf(leftNeck.time) : -1;
      const rightIndex = rightNeck ? rawTimes.indexOf(rightNeck.time) : -1;
      const end = Math.max(rightIndex, rawTimes.indexOf(signalTime(selectedSignal)));
      if (!leftNeck || !rightNeck || start < 0 || rightIndex < start || end < start) return [];
      const values = new Array(candles.length).fill(null) as Array<number | null>;
      const span = Math.max(1, rightIndex - start);
      for (let item = start; item <= end; item += 1) {
        values[item] = leftNeck.price + ((rightNeck.price - leftNeck.price) * (item - start)) / span;
      }
      return [{
        name: "颈线",
        type: "line" as const,
        data: values,
        symbol: "none",
        silent: true,
        lineStyle: { color: "#b7791f", width: 1.5, type: "dashed" as const },
      }];
    })() : [];
    const allNecklineSeries = series.chart.necklines.map((line, index) => {
      const values = new Array(candles.length).fill(null) as Array<number | null>;
      const start = Math.max(0, line.from_index);
      const end = Math.min(candles.length - 1, line.to_index);
      const span = Math.max(1, end - start);
      for (let item = start; item <= end; item += 1) {
        values[item] = line.from_price + ((line.to_price - line.from_price) * (item - start)) / span;
      }
      return {
        name: `颈线${index + 1}`,
        type: "line" as const,
        data: values,
        symbol: "none",
        silent: true,
        lineStyle: { color: line.confirmed ? "#b7791f" : "#7a8582", width: 1, type: "dashed" as const },
      };
    });
    const necklineSeries = selectedSignal ? selectedNecklineSeries : allNecklineSeries;
    const selectedIndexes = selectedSignal ? [
      ...STRUCTURE_POINTS.map(([key]) => signalPoint(selectedSignal, key)).map((point) => point ? rawTimes.indexOf(point.time) : -1),
      order?.entry_time ? rawTimes.indexOf(order.entry_time) : -1,
      order?.exit_time ? rawTimes.indexOf(order.exit_time) : -1,
    ].filter((index) => index >= 0) : [];
    const defaultZoomStart = Math.max(0, 100 - Math.min(100, 12000 / Math.max(candles.length, 1)));
    const zoomStart = selectedIndexes.length ? Math.max(0, ((Math.min(...selectedIndexes) - 12) / Math.max(candles.length - 1, 1)) * 100) : defaultZoomStart;
    const zoomEnd = selectedIndexes.length ? Math.min(100, ((Math.max(...selectedIndexes) + 12) / Math.max(candles.length - 1, 1)) * 100) : 100;
    chart.setOption({
      animation: false,
      backgroundColor: "#fbfcfb",
      legend: { top: 8, left: 10, data: ["K线", "MA5", "MA10", "MA20", "MA30", "MA60"], textStyle: { color: "#68736f", fontSize: 11 } },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        borderColor: "#cad4d0",
        backgroundColor: "rgba(255,255,255,.96)",
        formatter: (params: any) => {
          const items = Array.isArray(params) ? params : [params];
          const candle = items.find((item) => item?.seriesType === "candlestick");
          const index = Number(candle?.dataIndex);
          const candleData = candles[index];
          if (!candleData) return "";
          return [
            `<div>${candle.axisValueLabel || ""}</div>`,
            `<div>开盘价 <strong>${candleData.open}</strong></div>`,
            `<div>收盘价 <strong>${candleData.close}</strong></div>`,
            `<div>最低价 <strong>${candleData.low}</strong></div>`,
            `<div>最高价 <strong>${candleData.high}</strong></div>`,
          ].join("");
        },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 58, right: 24, top: 42, height: "62%" },
        { left: 58, right: 24, top: "76%", height: "12%" },
      ],
      xAxis: [
        { type: "category", data: times, boundaryGap: true, axisLine: { lineStyle: { color: "#aab5b1" } }, axisLabel: { show: false } },
        { type: "category", gridIndex: 1, data: times, boundaryGap: true, axisLine: { lineStyle: { color: "#aab5b1" } }, axisLabel: { color: "#75807c", formatter: (value: string) => value.slice(5, 16) } },
      ],
      yAxis: [
        { scale: true, splitLine: { lineStyle: { color: "#e8ecea" } }, axisLabel: { color: "#75807c" } },
        { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { color: "#75807c" }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: zoomStart, end: zoomEnd },
        { type: "slider", xAxisIndex: [0, 1], start: zoomStart, end: zoomEnd, bottom: 2, height: 18, borderColor: "#ded8d6", fillerColor: "rgba(179,58,58,.14)" },
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          data: candleValues,
          itemStyle: { color: "#c23b32", color0: "#0c7a5a", borderColor: "#c23b32", borderColor0: "#0c7a5a" },
          markPoint: { symbol: "pin", symbolSize: 36, label: { fontSize: 10, fontWeight: 700 }, data: markPoints },
        },
        { name: "MA5", type: "line", data: ma5, symbol: "none", lineStyle: { color: "#1168a8", width: 1 } },
        { name: "MA10", type: "line", data: ma10, symbol: "none", lineStyle: { color: "#6f4a8e", width: 1 } },
        { name: "MA20", type: "line", data: ma20, symbol: "none", lineStyle: { color: "#b7791f", width: 1 } },
        { name: "MA30", type: "line", data: ma30, symbol: "none", lineStyle: { color: "#16805b", width: 1 } },
        { name: "MA60", type: "line", data: ma60, symbol: "none", lineStyle: { color: "#b33a3a", width: 1 } },
        ...necklineSeries,
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: candles.map((item) => ({ value: item.volume, itemStyle: { color: item.close >= item.open ? "rgba(194,59,50,.45)" : "rgba(12,122,90,.45)" } })),
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(elementRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [series, order]);

  return <div ref={elementRef} className="backtest-chart" />;
}
