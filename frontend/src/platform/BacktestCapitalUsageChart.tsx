import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { formatMarketDateTime } from "../time";
import type { BacktestCapitalUsage } from "./types";


echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

function money(value: number) {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function BacktestCapitalUsageChart({ usage }: { usage: BacktestCapitalUsage }) {
  const elementRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!elementRef.current) return;
    const points = usage.items.map((item) => ({
      time: item.time,
      usedMargin: Number(item.used_margin),
      totalFunds: Number(item.total_funds),
      usageRate: Number(item.usage_rate),
    }));
    const labels = points.map((item) => formatMarketDateTime(item.time));
    const chart = echarts.init(elementRef.current, undefined, { renderer: "canvas" });
    chart.setOption({
      animation: false,
      grid: { top: 30, right: 36, bottom: 76, left: 82 },
      tooltip: {
        trigger: "axis",
        formatter: (params: Array<{ dataIndex: number }>) => {
          const point = points[params[0]?.dataIndex];
          if (!point) return "";
          return `${formatMarketDateTime(point.time)}<br/>资金使用率: ${point.usageRate.toFixed(2)}%<br/>占用保证金: ${money(point.usedMargin)}<br/>总资金: ${money(point.totalFunds)}`;
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
        name: "资金使用率",
        min: 0,
        nameTextStyle: { color: "#72807a" },
        axisLabel: { color: "#72807a", formatter: (value: number) => `${value.toFixed(0)}%` },
        splitLine: { lineStyle: { color: "#e7ece9" } },
      },
      series: [{
        type: "line",
        data: points.map((item) => item.usageRate),
        step: "end",
        symbol: "circle",
        showSymbol: points.length <= 80,
        symbolSize: 6,
        lineStyle: { width: 2.5, color: "#8b6c43" },
        itemStyle: { color: "#8b6c43", borderColor: "#fffdfc", borderWidth: 1 },
        areaStyle: { color: "rgba(139, 108, 67, 0.12)" },
      }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [usage]);

  return <div ref={elementRef} className="backtest-capital-usage-chart" />;
}
