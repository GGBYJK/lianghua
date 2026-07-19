import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { formatMarketDateTime } from "../time";
import type { BacktestEquityCurve } from "./types";


echarts.use([LineChart, GridComponent, MarkLineComponent, TooltipComponent, CanvasRenderer]);

const POSITIVE = "#b33a3a";
const NEGATIVE = "#16805b";

type Point = {
  time: string;
  netPnl: number;
  cumulativeNetPnl: number;
  isStart: boolean;
};

function money(value: number) {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function segmentColor(from: number, to: number) {
  return (from + to) / 2 < 0 ? NEGATIVE : POSITIVE;
}

export function BacktestEquityChart({ curve }: { curve: BacktestEquityCurve }) {
  const elementRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!elementRef.current) return;
    const closedPoints: Point[] = curve.items.map((item) => ({
      time: item.time,
      netPnl: Number(item.net_pnl),
      cumulativeNetPnl: Number(item.cumulative_net_pnl),
      isStart: false,
    }));
    const firstPoint = closedPoints[0];
    const points: Point[] = firstPoint ? [{
      time: curve.items[0].entry_time || firstPoint.time,
      netPnl: 0,
      cumulativeNetPnl: 0,
      isStart: true,
    }, ...closedPoints] : [];
    const labels = points.map((item) => formatMarketDateTime(item.time));
    const chart = echarts.init(elementRef.current, undefined, { renderer: "canvas" });
    const segments = points.slice(1).map((point, index) => ({
      type: "line" as const,
      data: points.map((item, pointIndex) => pointIndex === index || pointIndex === index + 1 ? item.cumulativeNetPnl : null),
      symbol: "none",
      connectNulls: false,
      lineStyle: { width: 2.5, color: segmentColor(points[index].cumulativeNetPnl, point.cumulativeNetPnl) },
      z: 2,
    }));
    chart.setOption({
      animation: false,
      grid: { top: 30, right: 36, bottom: 76, left: 82 },
      tooltip: {
        trigger: "axis",
        formatter: (params: Array<{ dataIndex: number }>) => {
          const point = points[params[0]?.dataIndex];
          if (!point) return "";
          return point.isStart
            ? `${formatMarketDateTime(point.time)}<br/>起始净收益: ${money(0)}`
            : `${formatMarketDateTime(point.time)}<br/>本笔净收益: ${money(point.netPnl)}<br/>累计净收益: ${money(point.cumulativeNetPnl)}`;
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: labels,
        axisLabel: {
          color: "#72807a",
          hideOverlap: true,
          formatter: (value: string) => value.replace(" ", "\n"),
        },
        axisLine: { lineStyle: { color: "#c9d2ce" } },
      },
      yAxis: {
        type: "value",
        name: "净收益",
        nameTextStyle: { color: "#72807a" },
        axisLabel: { color: "#72807a", formatter: (value: number) => money(value) },
        splitLine: { lineStyle: { color: "#e7ece9" } },
      },
      series: [
        ...segments,
        {
          type: "line" as const,
          data: points.map((item) => item.cumulativeNetPnl),
          symbol: "circle",
          showSymbol: points.length <= 80,
          symbolSize: 6,
          lineStyle: { opacity: 0 },
          itemStyle: { color: "#6f7b77", borderColor: "#fffdfc", borderWidth: 1 },
          markLine: { symbol: "none", lineStyle: { color: "#98aaa3", type: "dashed" }, data: [{ yAxis: 0 }] },
          z: 3,
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [curve]);

  return <div ref={elementRef} className="backtest-equity-chart" />;
}
