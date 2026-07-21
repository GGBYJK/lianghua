import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
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
  MarkAreaComponent,
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

export function BacktestChart({ series, orders }: { series: BacktestSeries; orders: BacktestOrder[] }) {
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
    const priceRange = Math.max(...candles.map((item) => item.high)) - Math.min(...candles.map((item) => item.low));
    const markerOffset = Math.max(priceRange * 0.06, 1);
    const markerUpperBound = Math.max(...candles.map((item) => item.high)) + priceRange * 0.1;
    const markerLowerBound = Math.min(...candles.map((item) => item.low)) - priceRange * 0.1;
    const singleOrderView = orders.length === 1;
    const markPoints: Array<Record<string, unknown>> = [];
    const markerConnectorLines: Array<Array<Record<string, unknown>>> = [];
    const markerPlacementOffset = (index: number, price: number, preferredOffset: number, fixedDirection = false) => {
      if (!preferredOffset) return preferredOffset;
      if (fixedDirection) return preferredOffset;
      const start = Math.max(0, index - 2);
      const end = Math.min(candles.length - 1, index + 2);
      const localHigh = Math.max(...candles.slice(start, end + 1).map((item) => item.high));
      const localLow = Math.min(...candles.slice(start, end + 1).map((item) => item.low));
      const upperSpace = markerUpperBound - localHigh;
      const lowerSpace = localLow - markerLowerBound;
      const magnitude = Math.abs(preferredOffset);
      const upperOffset = Math.max(magnitude, localHigh - price + magnitude * 0.5);
      const lowerOffset = Math.max(magnitude, price - localLow + magnitude * 0.5);
      const canPlaceUpper = upperSpace >= upperOffset;
      const canPlaceLower = lowerSpace >= lowerOffset;
      const preferredUpper = preferredOffset > 0;
      if (preferredUpper && canPlaceUpper) return upperOffset;
      if (!preferredUpper && canPlaceLower) return -lowerOffset;
      if (canPlaceUpper && !canPlaceLower) return upperOffset;
      if (canPlaceLower && !canPlaceUpper) return -lowerOffset;
      return upperSpace >= lowerSpace ? upperOffset : -lowerOffset;
    };
    const addMarker = (index: number, price: number, name: string, color: string, offset = 0, arrowGap = 0.12, symbolRotate = 0, labelInside = false, symbol?: string, symbolSize?: number | number[], fixedDirection = false) => {
      const placedOffset = markerPlacementOffset(index, price, offset, fixedDirection);
      const displayPrice = price + placedOffset;
      markPoints.push({ coord: [index, displayPrice], name, value: name, symbol, symbolSize, symbolRotate, label: labelInside ? { show: true, position: "inside", align: "center", verticalAlign: "middle", offset: [0, 0], rotate: 0, color: "#fff", fontSize: 10, fontWeight: 700 } : undefined, itemStyle: { color } });
      if (placedOffset) {
        const arrowTargetPrice = price + placedOffset * arrowGap;
        markerConnectorLines.push([{ coord: [index, displayPrice] }, { coord: [index, arrowTargetPrice] }]);
      }
    };
    const necklineSeries = orders.flatMap((order, orderIndex) => {
      const signal = order.signal;
      STRUCTURE_POINTS.forEach(([key, label]) => {
        const point = signalPoint(signal, key);
        const index = point ? rawTimes.indexOf(point.time) : -1;
        const isInverse = signal.pattern === "inverse_head_shoulders";
        const isNeck = key.includes("neck");
        const structureOffset = isInverse
          ? isNeck ? markerOffset * 0.8 : -markerOffset
          : isNeck ? -markerOffset * 0.8 : markerOffset;
        if (index >= 0 && point) addMarker(
          index,
          (candles[index].open + candles[index].close) / 2,
          label,
          "#6f4a46",
          structureOffset,
          0.42,
          0,
          signal.pattern === "head_shoulders_top" && key.includes("neck"),
          signal.pattern === "head_shoulders_top" && key.includes("neck") ? "roundRect" : undefined,
          signal.pattern === "head_shoulders_top" && key.includes("neck") ? [28, 18] : undefined,
          true,
        );
      });
      if (order.entry_time && order.entry_price != null) {
        const index = rawTimes.indexOf(order.entry_time);
        if (index >= 0) addMarker(index, (candles[index].open + candles[index].close) / 2, "进", "#1168a8", markerOffset, 0.42);
      }
      if (order.partial_exit_time && order.partial_exit_price != null) {
        const index = rawTimes.indexOf(order.partial_exit_time);
        if (index >= 0) addMarker(index, candles[index].close, "减", "#b7791f", markerOffset, 0.42);
      }
      if (order.exit_time && order.exit_price != null) {
        const index = rawTimes.indexOf(order.exit_time);
        if (index >= 0) addMarker(index, candles[index].close, "出", order.exit_reason === "TAKE_PROFIT" ? "#b33a3a" : "#16805b", markerOffset, 0.42);
      }
      const leftNeck = signalPoint(signal, "left_neck");
      const rightNeck = signalPoint(signal, "right_neck");
      const start = leftNeck ? rawTimes.indexOf(leftNeck.time) : -1;
      const rightIndex = rightNeck ? rawTimes.indexOf(rightNeck.time) : -1;
      const end = Math.max(rightIndex, rawTimes.indexOf(signalTime(signal)));
      if (!leftNeck || !rightNeck || start < 0 || rightIndex < start || end < start) return [];
      const values = new Array(candles.length).fill(null) as Array<number | null>;
      const span = Math.max(1, rightIndex - start);
      for (let item = start; item <= end; item += 1) {
        values[item] = leftNeck.price + ((rightNeck.price - leftNeck.price) * (item - start)) / span;
      }
      return [{
        name: `颈线${orderIndex + 1}`,
        type: "line" as const,
        data: values,
        symbol: "none",
        silent: true,
        lineStyle: { color: "#b7791f", width: 1.5, type: "dashed" as const },
      }];
    });
    const orderLevelSeries = orders.length === 1 ? (() => {
      const order = orders[0];
      const entryIndex = order.entry_time ? rawTimes.indexOf(order.entry_time) : -1;
      const rightShoulder = signalPoint(order.signal, "right_shoulder");
      const rightShoulderIndex = rightShoulder ? rawTimes.indexOf(rightShoulder.time) : -1;
      const exitIndex = order.exit_time ? rawTimes.indexOf(order.exit_time) : candles.length - 1;
      const endIndex = Math.max(entryIndex, exitIndex);
      if (entryIndex < 0 || endIndex < entryIndex) return [];
      const startIndex = rightShoulderIndex >= 0 ? Math.min(entryIndex, rightShoulderIndex) : entryIndex;
      const level = (name: string, price: number | string | null, color: string) => {
        if (price == null || !Number.isFinite(Number(price))) return null;
        const values = new Array(candles.length).fill(null) as Array<number | null>;
        for (let index = startIndex; index <= Math.min(endIndex, candles.length - 1); index += 1) values[index] = Number(price);
        return { name, type: "line" as const, data: values, symbol: "none", silent: true, lineStyle: { color, width: 1.5, type: "solid" as const } };
      };
      return [
        level("止损价", order.stop_price, "#16805b"),
        level("目标价", order.target_price, "#b33a3a"),
      ].filter((item): item is NonNullable<typeof item> => Boolean(item));
    })() : [];
    const holdingAreas = orders.flatMap((order) => {
      const entryIndex = order.entry_time ? rawTimes.indexOf(order.entry_time) : -1;
      const exitIndex = order.exit_time ? rawTimes.indexOf(order.exit_time) : candles.length - 1;
      return entryIndex >= 0 && exitIndex >= entryIndex ? [[{ xAxis: entryIndex }, { xAxis: exitIndex }]] : [];
    });
    const orderHoldingArea = holdingAreas.length ? {
      silent: true,
      itemStyle: { color: "rgba(17, 104, 168, 0.07)" },
      data: holdingAreas,
    } : undefined;
    const selectedIndexes = orders.length === 1 ? [
      ...STRUCTURE_POINTS.map(([key]) => signalPoint(orders[0].signal, key)).map((point) => point ? rawTimes.indexOf(point.time) : -1),
      orders[0].entry_time ? rawTimes.indexOf(orders[0].entry_time) : -1,
      orders[0].partial_exit_time ? rawTimes.indexOf(orders[0].partial_exit_time) : -1,
      orders[0].exit_time ? rawTimes.indexOf(orders[0].exit_time) : -1,
    ].filter((index) => index >= 0) : [];
    const defaultZoomStart = Math.max(0, 100 - Math.min(100, 12000 / Math.max(candles.length, 1)));
    const zoomStart = selectedIndexes.length ? Math.max(0, ((Math.min(...selectedIndexes) - 12) / Math.max(candles.length - 1, 1)) * 100) : defaultZoomStart;
    const zoomEnd = selectedIndexes.length ? Math.min(100, ((Math.max(...selectedIndexes) + 12) / Math.max(candles.length - 1, 1)) * 100) : 100;
    chart.setOption({
      animation: false,
      backgroundColor: "#ffffff",
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
          markLine: { symbol: ["none", "arrow"], symbolSize: 10, silent: true, lineStyle: { color: "#586560", width: 1.4, type: "dashed" }, data: markerConnectorLines },
          markArea: orderHoldingArea,
        },
        { name: "MA5", type: "line", data: ma5, symbol: "none", lineStyle: { color: "#1168a8", width: 1 } },
        { name: "MA10", type: "line", data: ma10, symbol: "none", lineStyle: { color: "#6f4a8e", width: 1 } },
        { name: "MA20", type: "line", data: ma20, symbol: "none", lineStyle: { color: "#b7791f", width: 1 } },
        { name: "MA30", type: "line", data: ma30, symbol: "none", lineStyle: { color: "#16805b", width: 1 } },
        { name: "MA60", type: "line", data: ma60, symbol: "none", lineStyle: { color: "#b33a3a", width: 1 } },
        ...necklineSeries,
        ...orderLevelSeries,
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
  }, [series, orders]);

  return <div ref={elementRef} className="backtest-chart" />;
}
