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
import { CircleHelp, Clock3, Database, Eraser, Eye, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { formatApiDateTime, formatMarketDateTime } from "../time";
import { platformApi } from "./api";
import type { KlineBar, KlineDataset, KlineDatasetStatus, KlineSyncJob } from "./types";


const TIMEFRAME_OPTIONS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"].map((value) => ({ value, label: value }));
const TIMEFRAME_ORDER = new Map(TIMEFRAME_OPTIONS.map((option, index) => [option.value, index]));
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);

type KlineDatasetGroup = {
  symbol: string;
  items: KlineDataset[];
};

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

function groupedDatasetStatus(items: KlineDataset[]): KlineDatasetStatus {
  const priority: KlineDatasetStatus[] = ["RUNNING", "QUEUED", "FAILED", "IDLE", "COMPLETED"];
  return priority.find((status) => items.some((item) => item.status === status)) || "IDLE";
}

function latestSuccessfulSync(items: KlineDataset[]): string | null {
  return items.reduce<string | null>((latest, item) => {
    if (!item.last_synced_at) return latest;
    if (!latest || new Date(item.last_synced_at).getTime() > new Date(latest).getTime()) return item.last_synced_at;
    return latest;
  }, null);
}

function formatFeatureNumber(value: string | number | null | undefined, digits = 4) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "--";
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
    onSuccess: async (result) => {
      api.success(`已创建 ${result.length} 个数据集，首次拉取已进入串行队列`);
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
  const groupAutoMutation = useMutation({
    mutationFn: ({ items, checked }: { items: KlineDataset[]; checked: boolean }) => Promise.all(
      items.map((item) => platformApi.updateKlineDataset(item.id, { auto_update: checked })),
    ),
    onSuccess: async () => {
      api.success("该品种全部周期的自动更新已同步");
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
  const createSymbol = Form.useWatch("symbol", createForm);
  const createTimeframeOptions = useMemo(() => {
    const existing = new Set(
      datasets
        .filter((item) => item.symbol === createSymbol)
        .map((item) => item.timeframe),
    );
    return TIMEFRAME_OPTIONS.map((option) => ({
      ...option,
      disabled: existing.has(option.value),
      label: existing.has(option.value) ? `${option.label} · 已存在` : option.label,
    }));
  }, [createSymbol, datasets]);
  const groupedRows = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    const visibleSymbols = new Set(
      datasets
        .filter((item) => (
          (!normalizedKeyword || item.symbol.toLowerCase().includes(normalizedKeyword))
          && (!timeframe || item.timeframe === timeframe)
        ))
        .map((item) => item.symbol),
    );
    const groups = new Map<string, KlineDataset[]>();
    datasets.forEach((item) => {
      if (!visibleSymbols.has(item.symbol)) return;
      const group = groups.get(item.symbol) || [];
      group.push(item);
      groups.set(item.symbol, group);
    });
    return Array.from(groups, ([symbol, items]) => ({
      symbol,
      items: items.sort((left, right) => (
        (TIMEFRAME_ORDER.get(left.timeframe) ?? 99) - (TIMEFRAME_ORDER.get(right.timeframe) ?? 99)
      )),
    })).sort((left, right) => left.symbol.localeCompare(right.symbol));
  }, [datasets, keyword, timeframe]);
  const visiblePeriodCount = useMemo(
    () => groupedRows.reduce((total, group) => total + group.items.length, 0),
    [groupedRows],
  );
  const stats = useMemo(() => ({
    datasets: datasets.length,
    rows: datasets.reduce((sum, item) => sum + item.row_count, 0),
    automatic: datasets.filter((item) => item.auto_update).length,
  }), [datasets]);

  const datasetColumns: ColumnsType<KlineDatasetGroup> = [
    {
      title: "品种 / 周期",
      width: "10%",
      className: "kline-summary-meta-cell",
      render: (_, row) => <div className="kline-symbol-summary">
        <strong>{row.symbol}</strong>
        <div>{row.items.map((item) => <Tag key={item.id}>{item.timeframe}</Tag>)}</div>
      </div>,
    },
    { title: "行情源", width: "6%", className: "kline-provider-cell", render: (_, row) => String(row.items[0]?.provider || "--").toUpperCase() },
    {
      title: "状态",
      width: "7%",
      className: "kline-summary-meta-cell",
      render: (_, row) => {
        const errors = row.items.map((item) => item.last_error).filter(Boolean).join("\n");
        const tag = statusTag(groupedDatasetStatus(row.items));
        return errors ? <Tooltip title={<span className="pre-line">{errors}</span>}>{tag}</Tooltip> : tag;
      },
    },
    {
      title: "数据量",
      width: "10%",
      render: (_, row) => <div className="kline-period-stack kline-count-stack">
        {row.items.map((item) => <div className="kline-period-row" key={item.id}>
          <span className="kline-period-key">{item.timeframe}</span>
          <strong>{item.row_count.toLocaleString()}</strong>
          <small>{["1h", "1d"].includes(item.timeframe) ? "5m合成" : `/ ${item.target_count.toLocaleString()}`}</small>
        </div>)}
      </div>,
    },
    {
      title: <span className="kline-feature-column-title">预计算缓存<Tooltip title="除原始K线外，还会预缓存成交量（VOL）、MA、MACD；1m、3m、5m周期额外缓存每根K线的多头和空头趋势评分。"><CircleHelp size={13} /></Tooltip></span>,
      width: "10%",
      render: (_, row) => <div className="kline-period-stack kline-feature-stack">
        {row.items.map((item) => {
          const ready = item.features_ready;
          return <Tooltip key={item.id} title={item.features_updated_at ? `计算于 ${formatApiDateTime(item.features_updated_at)}` : "下次数据更新时自动生成"}>
            <div className="kline-period-row">
              <span className="kline-period-key">{item.timeframe}</span>
              <Tag color={ready ? "success" : "default"}>{ready ? `${item.feature_row_count.toLocaleString()} 条` : "待生成"}</Tag>
            </div>
          </Tooltip>;
        })}
      </div>,
    },
    {
      title: "数据范围",
      width: "22%",
      render: (_, row) => <div className="kline-period-stack kline-range-stack">
        {row.items.map((item) => <div className="kline-period-row" key={item.id}>
          <span className="kline-period-key">{item.timeframe}:</span>
          {item.start_time && item.end_time
            ? <span>{formatMarketDateTime(item.start_time)} <i>至</i> {formatMarketDateTime(item.end_time)}</span>
            : <span className="kline-empty-value">尚未拉取</span>}
        </div>)}
      </div>,
    },
    {
      title: "凌晨自动更新",
      width: "8%",
      align: "center",
      className: "kline-summary-meta-cell",
      render: (_, row) => {
        const enabledCount = row.items.filter((item) => item.auto_update).length;
        const mixed = enabledCount > 0 && enabledCount < row.items.length;
        return <Tooltip title={mixed ? "部分周期的自动更新设置不一致，操作后将全部同步" : undefined}>
          <Switch
            checked={enabledCount === row.items.length}
            loading={groupAutoMutation.isPending && groupAutoMutation.variables?.items[0]?.symbol === row.symbol}
            onChange={(checked) => groupAutoMutation.mutate({ items: row.items, checked })}
          />
        </Tooltip>;
      },
    },
    {
      title: "最后成功更新",
      width: "12%",
      className: "kline-summary-meta-cell",
      render: (_, row) => {
        const latest = latestSuccessfulSync(row.items);
        return latest ? formatApiDateTime(latest) : "--";
      },
    },
    {
      title: "周期操作",
      width: "15%",
      render: (_, row) => <div className="kline-period-stack kline-action-stack">
        {row.items.map((item) => <div className="kline-period-row" key={item.id}>
          <span className="kline-period-key">{item.timeframe}</span>
          <Space size={0}>
            <Tooltip title="查看K线"><Button type="text" icon={<Eye size={15} />} disabled={!item.row_count} onClick={() => { setViewing(item); setBarPage(1); }} /></Tooltip>
            <Tooltip title="立即更新"><Button type="text" icon={<RefreshCw size={15} />} loading={syncMutation.isPending && syncMutation.variables === item.id} disabled={ACTIVE_STATUSES.has(item.status)} onClick={() => syncMutation.mutate(item.id)} /></Tooltip>
            <Tooltip title="修改容量"><Button type="text" icon={<Pencil size={15} />} onClick={() => { setEditing(item); editForm.setFieldsValue({ target_count: item.target_count }); }} /></Tooltip>
            <Popconfirm title={`删除 ${item.timeframe} K线数据集？`} description="维护配置和全部历史K线会一并删除。" onConfirm={() => deleteMutation.mutate(item.id)}>
              <Tooltip title="删除"><Button type="text" danger icon={<Trash2 size={15} />} disabled={item.status === "RUNNING"} /></Tooltip>
            </Popconfirm>
          </Space>
        </div>)}
      </div>,
    },
  ];

  const barColumns: ColumnsType<KlineBar> = [
    { title: "K线时间", dataIndex: "bar_time", width: 180, render: (value) => formatMarketDateTime(value) },
    { title: "开", dataIndex: "open", align: "right" },
    { title: "高", dataIndex: "high", align: "right" },
    { title: "低", dataIndex: "low", align: "right" },
    { title: "收", dataIndex: "close", align: "right" },
    { title: "成交量", dataIndex: "volume", align: "right" },
    {
      title: "MA缓存",
      width: 210,
      render: (_, row) => row.ma && Object.keys(row.ma).length
        ? <div className="kline-feature-values">{Object.entries(row.ma).map(([period, value]) => <span key={period}>MA{period}: {formatFeatureNumber(value, 2)}</span>)}</div>
        : "--",
    },
    {
      title: "MACD缓存",
      width: 180,
      render: (_, row) => row.macd_dif !== undefined
        ? <div className="kline-feature-values"><span>DIF: {formatFeatureNumber(row.macd_dif)}</span><span>DEA: {formatFeatureNumber(row.macd_dea)}</span><span>HIST: {formatFeatureNumber(row.macd_hist)}</span></div>
        : "--",
    },
    {
      title: "趋势评分",
      width: 130,
      render: (_, row) => row.trend_bullish !== null && row.trend_bullish !== undefined
        ? <div className="kline-feature-values"><span className="trend-bullish-value">多头: {row.trend_bullish}</span><span className="trend-bearish-value">空头: {row.trend_bearish}</span></div>
        : "--",
    },
  ];

  const jobColumns: ColumnsType<KlineSyncJob> = [
    { title: "品种 / 周期", width: 170, render: (_, row) => <strong>{row.symbol} · {row.timeframe}</strong> },
    { title: "触发方式", dataIndex: "trigger_type", width: 110, render: triggerLabel },
    { title: "状态", dataIndex: "status", width: 100, render: (value) => statusTag(value) },
    { title: "目标上限 / 实际", width: 150, align: "right", render: (_, row) => `${row.requested_count.toLocaleString()} / ${row.fetched_count.toLocaleString()}` },
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
        <div><strong>维护数据集</strong><span>{groupedRows.length} 个品种 · {visiblePeriodCount} 个周期</span></div>
        <Space wrap>
          <Input.Search allowClear placeholder="搜索品种" value={keyword} onChange={(event) => setKeyword(event.target.value)} className="kline-search" />
          <Select allowClear placeholder="全部周期" value={timeframe} onChange={setTimeframe} options={TIMEFRAME_OPTIONS} className="kline-timeframe-filter" />
        </Space>
      </div>
      <Table
        rowKey="symbol"
        columns={datasetColumns}
        dataSource={groupedRows}
        loading={datasetsQuery.isLoading}
        rowClassName="kline-symbol-summary-row"
        tableLayout="fixed"
        pagination={{ pageSize: 10, showSizeChanger: false }}
      />
    </section>

    <section className="table-section kline-job-section">
      <div className="kline-table-toolbar"><div><strong>最近更新任务</strong><span><Clock3 size={14} /> 严格单任务执行</span></div></div>
      <Table rowKey="id" size="small" columns={jobColumns} dataSource={jobsQuery.data || []} loading={jobsQuery.isLoading} scroll={{ x: 1100 }} pagination={{ pageSize: 10, showSizeChanger: false }} />
    </section>

    <Modal title="新增K线数据集" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => createForm.submit()} confirmLoading={createMutation.isPending} okText="创建并拉取">
      <Alert showIcon type="info" message="创建后进入串行队列，不会在页面请求中直接拉取大量行情。" />
      <Form form={createForm} layout="vertical" initialValues={{ timeframes: [], target_count: 10000, auto_update: true }} onFinish={(values) => createMutation.mutate(values)}>
        <Form.Item name="symbol" label="品种" rules={[{ required: true, message: "请选择品种" }]}>
          <Select showSearch optionFilterProp="label" loading={contractsQuery.isLoading} options={(contractsQuery.data || []).map((item) => ({ value: item.symbol, label: `${item.symbol} · ${item.name}` }))} onChange={() => createForm.setFieldValue("timeframes", [])} />
        </Form.Item>
        <div className="kline-form-grid">
          <Form.Item name="timeframes" label="K线周期" rules={[{ required: true, message: "请至少选择一个周期" }]}><Select mode="multiple" maxTagCount="responsive" options={createTimeframeOptions} /></Form.Item>
          <Form.Item name="target_count" label="最多保留条数" rules={[{ required: true }]}><InputNumber min={120} max={10000} step={100} className="full-input" /></Form.Item>
        </div>
        <Form.Item name="auto_update" label="每日凌晨自动更新" valuePropName="checked"><Switch /></Form.Item>
        <Typography.Text type="secondary">1m–30m 直接获取；1h 和 1d 使用最多 10000 条 5m K线合成，按实际可用数量保存。</Typography.Text>
      </Form>
    </Modal>

    <Modal title={`调整维护容量${editing ? ` · ${editing.symbol} / ${editing.timeframe}` : ""}`} open={Boolean(editing)} onCancel={() => setEditing(null)} onOk={() => editForm.submit()} confirmLoading={updateMutation.isPending}>
      <Form form={editForm} layout="vertical" onFinish={(values) => editing && updateMutation.mutate({ id: editing.id, values })}>
        <Form.Item name="target_count" label="保留最新K线条数" rules={[{ required: true }]}><InputNumber min={120} max={10000} step={100} className="full-input" /></Form.Item>
        <Typography.Text type="secondary">调小容量会立即删除超出范围的最旧数据；调大后请执行一次更新以补充数据。</Typography.Text>
      </Form>
    </Modal>

    <Drawer width="min(1280px, 96vw)" title={viewing ? `${viewing.symbol} / ${viewing.timeframe} · K线明细` : "K线明细"} open={Boolean(viewing)} onClose={() => setViewing(null)} destroyOnHidden>
      {viewing ? <div className="kline-drawer-summary"><span>共 <strong>{viewing.row_count.toLocaleString()}</strong> 条</span><span>{viewing.start_time ? formatMarketDateTime(viewing.start_time) : "--"} 至 {viewing.end_time ? formatMarketDateTime(viewing.end_time) : "--"}</span></div> : null}
      <Table rowKey="bar_time" size="small" columns={barColumns} dataSource={barsQuery.data?.items || []} loading={barsQuery.isLoading} scroll={{ x: 1350 }} pagination={{ current: barPage, pageSize: 50, total: barsQuery.data?.total || 0, showSizeChanger: false, onChange: setBarPage }} />
    </Drawer>
  </div>;
}
