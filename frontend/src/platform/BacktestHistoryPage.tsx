import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Popconfirm, Progress, Space, Statistic, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Download, Eye, History, Plus, Trash2, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { formatApiDateTime } from "../time";
import { downloadBacktest, platformApi } from "./api";
import type { BacktestRun } from "./types";


const ACTIVE_STATUSES = new Set<BacktestRun["status"]>(["PENDING", "QUEUED", "RUNNING"]);

function statusTag(status: BacktestRun["status"], cancelRequested = false) {
  const labels: Record<BacktestRun["status"], string> = {
    PENDING: "排队中",
    QUEUED: "排队中",
    RUNNING: "运行中",
    COMPLETED: "已完成",
    COMPLETED_WITH_ERRORS: "部分完成",
    FAILED: "失败",
    CANCELLED: "已取消",
  };
  const colors: Partial<Record<BacktestRun["status"], string>> = {
    RUNNING: "processing",
    COMPLETED: "success",
    COMPLETED_WITH_ERRORS: "warning",
    FAILED: "error",
  };
  if (status === "RUNNING" && cancelRequested) return <Tag color="warning">取消中</Tag>;
  return <Tag color={colors[status]}>{labels[status]}</Tag>;
}

export default function BacktestHistoryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const runsQuery = useQuery({
    queryKey: ["backtests"],
    queryFn: platformApi.backtests,
    refetchInterval: (query) => (query.state.data as BacktestRun[] | undefined)?.some((item) => ACTIVE_STATUSES.has(item.status)) ? 2000 : false,
  });
  const rows = runsQuery.data || [];
  const stats = useMemo(() => ({
    total: rows.length,
    running: rows.filter((item) => ACTIVE_STATUSES.has(item.status)).length,
    completed: rows.filter((item) => item.status === "COMPLETED" || item.status === "COMPLETED_WITH_ERRORS").length,
    signals: rows.reduce((sum, item) => sum + item.signal_count, 0),
  }), [rows]);

  const cancelMutation = useMutation({
    mutationFn: platformApi.cancelBacktest,
    onSuccess: () => {
      api.success("已提交取消请求");
      void queryClient.invalidateQueries({ queryKey: ["backtests"] });
    },
    onError: (error: Error) => api.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: platformApi.deleteBacktest,
    onSuccess: () => {
      api.success("回测记录已删除");
      void queryClient.invalidateQueries({ queryKey: ["backtests"] });
    },
    onError: (error: Error) => api.error(error.message),
  });

  const columns: ColumnsType<BacktestRun> = [
    {
      title: "回测名称",
      dataIndex: "name",
      fixed: "left",
      width: 220,
      render: (value, row) => <Button type="link" className="history-name" onClick={() => navigate(`/analysis/backtest/${row.id}`)}>{value}</Button>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 150,
      render: (value, row) => <div className="backtest-history-status">{statusTag(value, row.cancel_requested)}{ACTIVE_STATUSES.has(row.status) ? <Progress percent={row.progress} size="small" showInfo={false} /> : null}</div>,
    },
    {
      title: "品种 / 周期",
      width: 220,
      render: (_, row) => <div className="backtest-history-markets"><strong>{row.request.symbols.join("、")}</strong><span>{row.request.timeframes.join(" / ")}</span></div>,
    },
    { title: "K线数", dataIndex: ["request", "kline_count"], width: 88, align: "right" },
    { title: "止盈条件", width: 96, align: "right", render: (_, row) => `${row.request.take_profit_rules.length} 项` },
    { title: "信号", dataIndex: "signal_count", width: 76, align: "right" },
    { title: "订单", dataIndex: "order_count", width: 76, align: "right" },
    { title: "组合进度", width: 100, align: "right", render: (_, row) => `${row.completed_combinations}/${row.total_combinations}` },
    { title: "创建时间", dataIndex: "created_at", width: 168, render: (value) => formatApiDateTime(value) },
    {
      title: "操作",
      fixed: "right",
      width: 150,
      render: (_, row) => <Space size={2}>
        <Tooltip title="查看详情"><Button type="text" icon={<Eye size={16} />} onClick={() => navigate(`/analysis/backtest/${row.id}`)} /></Tooltip>
        {!ACTIVE_STATUSES.has(row.status) ? <Tooltip title="导出Excel"><Button type="text" icon={<Download size={16} />} onClick={() => void downloadBacktest(row.id)} /></Tooltip> : null}
        {ACTIVE_STATUSES.has(row.status) && !row.cancel_requested ? <Tooltip title="取消回测"><Button type="text" danger icon={<X size={16} />} onClick={() => cancelMutation.mutate(row.id)} /></Tooltip> : null}
        {!ACTIVE_STATUSES.has(row.status) ? <Popconfirm title="删除这次回测？" description="汇总、订单和K线结构将一并删除。" onConfirm={() => deleteMutation.mutate(row.id)}><Tooltip title="删除"><Button type="text" danger icon={<Trash2 size={16} />} /></Tooltip></Popconfirm> : null}
      </Space>,
    },
  ];

  return <div className="backtest-history-page">
    {contextHolder}
    <header className="page-heading backtest-history-heading">
      <div><span className="page-kicker">BACKTEST ARCHIVE</span><Typography.Title level={2}>策略回测记录</Typography.Title><Typography.Text>查看历史回测结果，或创建一组新的品种、周期和止盈条件测试。</Typography.Text></div>
      <Button type="primary" icon={<Plus size={17} />} onClick={() => navigate("/analysis/backtest/new")}>添加回测</Button>
    </header>
    <section className="backtest-history-band">
      <div className="backtest-history-identity"><History size={22} /><span>个人回测档案</span></div>
      <Statistic title="全部记录" value={stats.total} />
      <Statistic title="正在运行" value={stats.running} />
      <Statistic title="已完成" value={stats.completed} />
      <Statistic title="累计识别信号" value={stats.signals} />
    </section>
    <section className="table-section backtest-history-table">
      <div className="table-toolbar"><strong>回测列表</strong><span className="table-count">共 {rows.length} 条</span></div>
      <Table rowKey="id" columns={columns} dataSource={rows} loading={runsQuery.isLoading} scroll={{ x: 1350 }} pagination={{ pageSize: 15, showSizeChanger: false }} onRow={(row) => ({ onDoubleClick: () => navigate(`/analysis/backtest/${row.id}`) })} />
    </section>
  </div>;
}
