import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { Clock3, Database, Eraser, Eye, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { formatApiDateTime, formatMarketDateTime } from "../time";
import { platformApi } from "./api";
import type { KlineBar, KlineDataset, KlineDatasetStatus, KlineSyncJob } from "./types";


const TIMEFRAME_OPTIONS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"].map((value) => ({ value, label: value }));
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);

const STATUS_META: Record<KlineDatasetStatus | KlineSyncJob["status"], { label: string; color?: string }> = {
  IDLE: { label: "就绪", color: "success" },
  QUEUED: { label: "排队中", color: "default" },
  RUNNING: { label: "更新中", color: "processing" },
  COMPLETED: { label: "已完成", color: "success" },
  FAILED: { label: "失败", color: "error" },
};

function statusTag(status: KlineDatasetStatus | KlineSyncJob["status"]) {
  const meta = STATUS_META[status];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function triggerLabel(trigger: KlineSyncJob["trigger_type"]) {
  return { INITIAL: "首次拉取", MANUAL: "手动更新", SCHEDULED: "凌晨更新" }[trigger];
}

export default function KlineDataPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<KlineDataset | null>(null);
  const [viewing, setViewing] = useState<KlineDataset | null>(null);
  const [barPage, setBarPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [timeframe, setTimeframe] = useState<string>();
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();

  const datasetsQuery = useQuery({
    queryKey: ["kline-datasets"],
    queryFn: platformApi.klineDatasets,
    refetchInterval: (query) => (query.state.data as KlineDataset[] | undefined)?.some((item) => ACTIVE_STATUSES.has(item.status)) ? 2000 : 15_000,
  });
  const jobsQuery = useQuery({
    queryKey: ["kline-sync-jobs"],
    queryFn: platformApi.klineSyncJobs,
    refetchInterval: (query) => (query.state.data as KlineSyncJob[] | undefined)?.some((item) => ACTIVE_STATUSES.has(item.status)) ? 2000 : 15_000,
  });
  const cacheQuery = useQuery({
    queryKey: ["analysis-cache-stats"],
    queryFn: platformApi.analysisCacheStats,
    refetchInterval: 15_000,
  });
  const contractsQuery = useQuery({
    queryKey: ["contract-center"],
    queryFn: platformApi.marketContracts,
    staleTime: 60_000,
  });
  const barsQuery = useQuery({
    queryKey: ["kline-bars", viewing?.id, barPage],
    queryFn: () => platformApi.klineBars(viewing!.id, barPage, 50),
    enabled: Boolean(viewing),
  });

  const invalidate = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["kline-datasets"] }),
    queryClient.invalidateQueries({ queryKey: ["kline-sync-jobs"] }),
  ]);
  const createMutation = useMutation({
    mutationFn: platformApi.createKlineDataset,
    onSuccess: async () => {
      api.success("数据集已创建，首次拉取已进入串行队列");
      setCreateOpen(false);
      createForm.resetFields();
      await invalidate();
    },
    onError: (error: Error) => api.error(error.message),
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: { target_count?: number; auto_update?: boolean } }) => platformApi.updateKlineDataset(id, values),
    onSuccess: async () => {
      api.success("维护配置已更新");
      setEditing(null);
      await invalidate();
    },
    onError: (error: Error) => api.error(error.message),
  });
  const syncMutation = useMutation({
    mutationFn: platformApi.syncKlineDataset,
    onSuccess: async () => {
      api.success("更新任务已进入串行队列");
      await invalidate();
    },
    onError: (error: Error) => api.error(error.message),
  });
  const syncAllMutation = useMutation({
    mutationFn: platformApi.syncAllKlineDatasets,
    onSuccess: async (result) => {
      api.success(`已检查 ${result.queued} 个数据集的更新队列`);
      await invalidate();
    },
    onError: (error: Error) => api.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: platformApi.deleteKlineDataset,
    onSuccess: async () => {
      api.success("数据集及其K线已删除");
      await invalidate();
    },
    onError: (error: Error) => api.error(error.message),
  });
  const clearCacheMutation = useMutation({
    mutationFn: platformApi.clearAnalysisCache,
    onSuccess: async (result) => {
      api.success(`已清理 ${result.deleted} 条分析缓存`);
      await queryClient.invalidateQueries({ queryKey: ["analysis-cache-stats"] });
    },
    onError: (error: Error) => api.error(error.message),
  });

  const datasets = datasetsQuery.data || [];
  const rows = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return datasets.filter((item) => (
      (!normalizedKeyword || item.symbol.toLowerCase().includes(normalizedKeyword))
      && (!timeframe || item.timeframe === timeframe)
    ));
  }, [datasets, keyword, timeframe]);
  const stats = useMemo(() => ({
    datasets: datasets.length,
    rows: datasets.reduce((sum, item) => sum + item.row_count, 0),
    automatic: datasets.filter((item) => item.auto_update).length,
  }), [datasets]);

  const datasetColumns: ColumnsType<KlineDataset> = [
    {
      title: "品种 / 周期",
      fixed: "left",
      width: 150,
      render: (_, row) => <div className="kline-market-cell"><strong>{row.symbol}</strong><Tag>{row.timeframe}</Tag></div>,
    },
    { title: "行情源", dataIndex: "provider", width: 90, className: "kline-provider-cell", render: (value) => String(value).toUpperCase() },
    { title: "状态", dataIndex: "status", width: 80, render: (value) => statusTag(value) },
    {
      title: "数据量",
      width: 140,
      render: (_, row) => <div className="kline-count-cell"><strong>{row.row_count.toLocaleString()}</strong><span>/ {row.target_count.toLocaleString()} 条</span></div>,
    },
    {
      title: "数据范围",
      width: 270,
      render: (_, row) => row.start_time && row.end_time
        ? <div className="kline-range-cell"><span>{formatMarketDateTime(row.start_time)}</span><i>至</i><span>{formatMarketDateTime(row.end_time)}</span></div>
        : <Typography.Text type="secondary">尚未拉取</Typography.Text>,
    },
    {
      title: "凌晨自动更新",
      width: 120,
      render: (_, row) => <Switch
        checked={row.auto_update}
        loading={updateMutation.isPending && updateMutation.variables?.id === row.id}
        onChange={(checked) => updateMutation.mutate({ id: row.id, values: { auto_update: checked } })}
      />,
    },
    {
      title: "最后成功更新",
      dataIndex: "last_synced_at",
      width: 160,
      render: (value, row) => row.last_error
        ? <Tooltip title={row.last_error}><Typography.Text type="danger">更新失败，查看任务记录</Typography.Text></Tooltip>
        : value ? formatApiDateTime(value) : "--",
    },
    {
      title: "操作",
      fixed: "right",
      width: 150,
      render: (_, row) => <Space size={2}>
        <Tooltip title="查看K线"><Button type="text" icon={<Eye size={16} />} disabled={!row.row_count} onClick={() => { setViewing(row); setBarPage(1); }} /></Tooltip>
        <Tooltip title="立即更新"><Button type="text" icon={<RefreshCw size={16} />} loading={syncMutation.isPending && syncMutation.variables === row.id} disabled={ACTIVE_STATUSES.has(row.status)} onClick={() => syncMutation.mutate(row.id)} /></Tooltip>
        <Tooltip title="修改容量"><Button type="text" icon={<Pencil size={16} />} onClick={() => { setEditing(row); editForm.setFieldsValue({ target_count: row.target_count }); }} /></Tooltip>
        <Popconfirm title="删除该K线数据集？" description="维护配置和全部历史K线会一并删除。" onConfirm={() => deleteMutation.mutate(row.id)}>
          <Tooltip title="删除"><Button type="text" danger icon={<Trash2 size={16} />} disabled={row.status === "RUNNING"} /></Tooltip>
        </Popconfirm>
      </Space>,
    },
  ];

  const barColumns: ColumnsType<KlineBar> = [
    { title: "K线时间", dataIndex: "bar_time", width: 180, render: (value) => formatMarketDateTime(value) },
    { title: "开", dataIndex: "open", align: "right" },
    { title: "高", dataIndex: "high", align: "right" },
    { title: "低", dataIndex: "low", align: "right" },
    { title: "收", dataIndex: "close", align: "right" },
    { title: "成交量", dataIndex: "volume", align: "right" },
  ];

  const jobColumns: ColumnsType<KlineSyncJob> = [
    { title: "品种 / 周期", width: 170, render: (_, row) => <strong>{row.symbol} · {row.timeframe}</strong> },
    { title: "触发方式", dataIndex: "trigger_type", width: 110, render: triggerLabel },
    { title: "状态", dataIndex: "status", width: 100, render: (value) => statusTag(value) },
    { title: "请求 / 拉取", width: 130, align: "right", render: (_, row) => `${row.requested_count.toLocaleString()} / ${row.fetched_count.toLocaleString()}` },
    { title: "写入", dataIndex: "written_count", width: 100, align: "right", render: (value) => Number(value).toLocaleString() },
    { title: "创建时间", dataIndex: "created_at", width: 170, render: (value) => formatApiDateTime(value) },
    { title: "完成时间", dataIndex: "completed_at", width: 170, render: (value) => value ? formatApiDateTime(value) : "--" },
    { title: "结果", dataIndex: "error_message", render: (value) => value ? <Typography.Text type="danger">{value}</Typography.Text> : "--" },
  ];

  return <div className="kline-data-page">
    {contextHolder}
    <header className="page-heading kline-data-heading">
      <div>
        <span className="page-kicker">MARKET DATA STORE</span>
        <Typography.Title level={2}>K线数据维护</Typography.Title>
        <Typography.Text>统一维护回测所需行情，命中数据集后回测直接读取数据库中的最新数据。</Typography.Text>
      </div>
      <Space>
        <Popconfirm
          title="清理分析缓存？"
          description="只删除可重新生成的研究结果，不会删除 K 线数据。"
          onConfirm={() => clearCacheMutation.mutate()}
        >
          <Button title="清理分析缓存" aria-label="清理分析缓存" icon={<Eraser size={16} />} loading={clearCacheMutation.isPending} disabled={!cacheQuery.data?.entries}>清理缓存</Button>
        </Popconfirm>
        <Button title="更新全部数据集" aria-label="更新全部数据集" icon={<RefreshCw size={16} />} loading={syncAllMutation.isPending} disabled={!datasets.length} onClick={() => syncAllMutation.mutate()}>更新全部</Button>
        <Button title="新增数据集" aria-label="新增数据集" type="primary" icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>新增数据集</Button>
      </Space>
    </header>

    <section className="kline-stat-band">
      <div className="kline-stat-identity"><Database size={22} /><span>数据库行情缓存</span><small>每日 03:00 串行更新</small></div>
      <Statistic title="数据集" value={stats.datasets} />
      <Statistic title="K线总量" value={stats.rows} />
      <Statistic title="自动更新" value={stats.automatic} />
      <Tooltip title={`占用 ${((cacheQuery.data?.bytes || 0) / 1024 / 1024).toFixed(2)} MB，累计节省 ${((cacheQuery.data?.calculation_ms || 0) / 1000).toFixed(1)} 秒计算`}>
        <Statistic title={`分析缓存 · 命中 ${cacheQuery.data?.hits || 0} 次`} value={cacheQuery.data?.entries || 0} suffix="项" />
      </Tooltip>
    </section>

    <section className="table-section kline-dataset-section">
      <div className="kline-table-toolbar">
        <div><strong>维护数据集</strong><span>共 {rows.length} 项</span></div>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索品种" value={keyword} onChange={(event) => setKeyword(event.target.value)} className="kline-search" />
          <Select allowClear placeholder="全部周期" value={timeframe} onChange={setTimeframe} options={TIMEFRAME_OPTIONS} className="kline-timeframe-filter" />
        </Space>
      </div>
      <Table rowKey="id" columns={datasetColumns} dataSource={rows} loading={datasetsQuery.isLoading} scroll={{ x: 1145 }} pagination={{ pageSize: 15, showSizeChanger: false }} />
    </section>

    <section className="table-section kline-job-section">
      <div className="kline-table-toolbar"><div><strong>最近更新任务</strong><span><Clock3 size={14} /> 严格单任务执行</span></div></div>
      <Table rowKey="id" size="small" columns={jobColumns} dataSource={jobsQuery.data || []} loading={jobsQuery.isLoading} scroll={{ x: 1100 }} pagination={{ pageSize: 10, showSizeChanger: false }} />
    </section>

    <Modal title="新增K线数据集" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => createForm.submit()} confirmLoading={createMutation.isPending} okText="创建并拉取">
      <Alert showIcon type="info" message="创建后进入串行队列，不会在页面请求中直接拉取大量行情。" />
      <Form form={createForm} layout="vertical" initialValues={{ timeframe: "3m", target_count: 10000, auto_update: true }} onFinish={(values) => createMutation.mutate(values)}>
        <Form.Item name="symbol" label="品种" rules={[{ required: true, message: "请选择品种" }]}>
          <Select showSearch optionFilterProp="label" loading={contractsQuery.isLoading} options={(contractsQuery.data || []).map((item) => ({ value: item.symbol, label: `${item.symbol} · ${item.name}` }))} />
        </Form.Item>
        <div className="kline-form-grid">
          <Form.Item name="timeframe" label="K线周期" rules={[{ required: true }]}><Select options={TIMEFRAME_OPTIONS} /></Form.Item>
          <Form.Item name="target_count" label="维护条数" rules={[{ required: true }]}><InputNumber min={120} max={10000} step={100} className="full-input" /></Form.Item>
        </div>
        <Form.Item name="auto_update" label="每日凌晨自动更新" valuePropName="checked"><Switch /></Form.Item>
      </Form>
    </Modal>

    <Modal title={`调整维护容量${editing ? ` · ${editing.symbol} / ${editing.timeframe}` : ""}`} open={Boolean(editing)} onCancel={() => setEditing(null)} onOk={() => editForm.submit()} confirmLoading={updateMutation.isPending}>
      <Form form={editForm} layout="vertical" onFinish={(values) => editing && updateMutation.mutate({ id: editing.id, values })}>
        <Form.Item name="target_count" label="保留最新K线条数" rules={[{ required: true }]}><InputNumber min={120} max={10000} step={100} className="full-input" /></Form.Item>
        <Typography.Text type="secondary">调小容量会立即删除超出范围的最旧数据；调大后请执行一次更新以补充数据。</Typography.Text>
      </Form>
    </Modal>

    <Drawer width="min(920px, 94vw)" title={viewing ? `${viewing.symbol} / ${viewing.timeframe} · K线明细` : "K线明细"} open={Boolean(viewing)} onClose={() => setViewing(null)} destroyOnHidden>
      {viewing ? <div className="kline-drawer-summary"><span>共 <strong>{viewing.row_count.toLocaleString()}</strong> 条</span><span>{viewing.start_time ? formatMarketDateTime(viewing.start_time) : "--"} 至 {viewing.end_time ? formatMarketDateTime(viewing.end_time) : "--"}</span></div> : null}
      <Table rowKey="bar_time" size="small" columns={barColumns} dataSource={barsQuery.data?.items || []} loading={barsQuery.isLoading} scroll={{ x: 760 }} pagination={{ current: barPage, pageSize: 50, total: barsQuery.data?.total || 0, showSizeChanger: false, onChange: setBarPage }} />
    </Drawer>
  </div>;
}
