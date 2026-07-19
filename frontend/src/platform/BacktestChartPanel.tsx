import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Empty, Spin } from "antd";

import { platformApi } from "./api";
import { BacktestChart } from "./BacktestChart";
import type { BacktestOrder } from "./types";


type Props = {
  runId: string;
  symbol: string;
  timeframe: string;
  ruleKey: string;
  selectedOrder: BacktestOrder | null;
};

export function BacktestChartPanel({ runId, symbol, timeframe, ruleKey, selectedOrder }: Props) {
  const seriesQuery = useQuery({
    queryKey: ["backtest-series", runId, symbol, timeframe],
    queryFn: () => platformApi.backtestSeries(runId, symbol, timeframe),
    staleTime: Infinity,
  });
  const orderParams = useMemo(() => new URLSearchParams({
    page: "1",
    page_size: "5000",
    symbol,
    timeframe,
    rule_key: ruleKey,
  }), [ruleKey, symbol, timeframe]);
  const ordersQuery = useQuery({
    queryKey: ["backtest-chart-orders", runId, orderParams.toString()],
    queryFn: () => platformApi.backtestOrders(runId, orderParams),
    enabled: Boolean(ruleKey),
    staleTime: Infinity,
  });
  const orders = ruleKey
    ? ordersQuery.data?.items || []
    : selectedOrder?.symbol === symbol && selectedOrder.timeframe === timeframe ? [selectedOrder] : [];

  return <section className="backtest-chart-panel">
    <div className="backtest-chart-panel-heading"><strong>{symbol} / {timeframe}</strong>{ruleKey ? <span>{ordersQuery.data?.total || 0} 笔订单</span> : selectedOrder?.symbol === symbol && selectedOrder.timeframe === timeframe ? <span>单笔订单</span> : null}</div>
    {seriesQuery.isLoading || (Boolean(ruleKey) && ordersQuery.isLoading) ? <div className="backtest-chart-loading"><Spin /></div> : seriesQuery.data ? <BacktestChart series={seriesQuery.data} orders={orders} /> : <Empty description="暂无K线结构" />}
  </section>;
}
