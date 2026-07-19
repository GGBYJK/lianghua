import { useEffect, useMemo, useRef, useState, type UIEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ArrowLeft, BarChart3, CircleHelp, Download, Eye, ListChecks, Play, Plus, RotateCcw, Save, Trash2, X } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { listContracts } from "../api";
import { downloadBacktest, platformApi } from "./api";
import { BacktestChartPanel } from "./BacktestChartPanel";
import { BacktestEquityChart } from "./BacktestEquityChart";
import type { BacktestOrder, BacktestRequest, BacktestRule, BacktestRun, BacktestSummary } from "./types";
import { formatApiDateTime, formatMarketDateTime } from "../time";


const TIMEFRAMES = [
  { label: "1分钟", value: "1m" }, { label: "3分钟", value: "3m" }, { label: "5分钟", value: "5m" },
  { label: "15分钟", value: "15m" }, { label: "30分钟", value: "30m" }, { label: "1小时", value: "1h" }, { label: "日线", value: "1d" },
];

const BUILTIN_RULES: BacktestRule[] = [
  { key: "pattern", label: "形态量度目标", type: "PATTERN_TARGET" },
];

const DEFAULT_RULE_KEYS = ["pattern"];
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);
const ENTRY_CONDITIONS: Array<{ label: string; value: BacktestRequest["entry_conditions"][number] }> = [
  { label: "头肩顶 · 做空（右肩触发）", value: "head_shoulders_top:right_shoulder_confirmed" },
  { label: "反向头肩 · 做多（右肩触发）", value: "inverse_head_shoulders:right_shoulder_confirmed" },
];
const OTHER_ENTRY_CONDITIONS: Array<{ label: string; value: BacktestRequest["other_entry_conditions"][number] }> = [
  { label: "头肩顶回抽-做多", value: "head_shoulders_top:head_shoulders_top_pullback" },
  { label: "反向头肩回抽-做空", value: "inverse_head_shoulders:inverse_head_shoulders_pullback" },
];

function dateTime(value: string | null | undefined) {
  return formatApiDateTime(value);
}

function number(value: unknown, digits = 2) {
  if (value == null || value === "") return "--";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function priceDigitsForTick(tick: unknown) {
  const value = Number(tick);
  if (!Number.isFinite(value) || value <= 0) return 4;
  if (value >= 1) return 0;

  let scaled = value;
  for (let digits = 0; digits < 8; digits += 1) {
    if (Math.abs(scaled - Math.round(scaled)) < 1e-8) return digits;
    scaled *= 10;
  }
  return 4;
}

function contractSymbolKeys(symbol: string) {
  const normalized = symbol.trim().toLowerCase();
  const keys = new Set([normalized]);
  const match = normalized.match(/^([a-z]+)\.([a-z]+)/);
  if (match) {
    keys.add(`${match[1]}.${match[2]}`);
    keys.add(match[2]);
  } else {
    const product = normalized.match(/^[a-z]+/)?.[0];
    if (product) keys.add(product);
  }
  return keys;
}

function statusTag(status: BacktestRun["status"]) {
  const labels: Record<string, string> = {
    QUEUED: "排队中", RUNNING: "运行中", COMPLETED: "已完成", COMPLETED_WITH_ERRORS: "部分完成", FAILED: "失败", CANCELLED: "已取消",
  };
  const colors: Record<string, string> = { QUEUED: "default", RUNNING: "processing", COMPLETED: "success", COMPLETED_WITH_ERRORS: "warning", FAILED: "error" };
  return <Tag color={colors[status]}>{labels[status] || status}</Tag>;
}

function exitTag(reason: BacktestOrder["exit_reason"], status: BacktestOrder["status"]) {
  if (status === "INCOMPLETE") return <Tag>数据不足</Tag>;
  if (status === "INVALID") return <Tag color="default">无效样本</Tag>;
  if (reason === "TAKE_PROFIT") return <Tag color="red">止盈</Tag>;
  if (reason === "STOP_LOSS") return <Tag color="green">止损</Tag>;
  return <Tag color="warning">到期平仓</Tag>;
}

function directionLabel(order: Pick<BacktestOrder, "pattern" | "alert_type" | "direction">) {
  if (order.alert_type === "head_shoulders_top_pullback") return "头肩顶反抽做多";
  if (order.alert_type === "inverse_head_shoulders_pullback") return "反向头肩反抽做空";
  if (order.pattern === "head_shoulders_top") return "头肩顶做空";
  if (order.pattern === "inverse_head_shoulders") return "反向头肩做多";
  return order.direction === "LONG" ? "做多" : "做空";
}

export default function BacktestPage() {
  const navigate = useNavigate();
  const { runId } = useParams();
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [form] = Form.useForm();
  const selectedRunId = runId || null;
  const [activeTab, setActiveTab] = useState("overview");
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ symbol: "", timeframe: "", rule_key: "", exit_reason: "" });
  const [chartSymbol, setChartSymbol] = useState("");
  const [chartTimeframes, setChartTimeframes] = useState<string[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<BacktestOrder | null>(null);
  const [equityRuleKey, setEquityRuleKey] = useState("");
  const [chartRuleKey, setChartRuleKey] = useState("");
  const [customRules, setCustomRules] = useState<BacktestRule[]>([]);
  const [customDraft, setCustomDraft] = useState({ type: "RR" as "RR" | "QTR", multiplier: 1 });
  const [selectedSymbolGroupId, setSelectedSymbolGroupId] = useState<string>();
  const [symbolGroupModalOpen, setSymbolGroupModalOpen] = useState(false);
  const [symbolGroupName, setSymbolGroupName] = useState("");
  const topOrderScrollRef = useRef<HTMLDivElement>(null);
  const orderTableRef = useRef<HTMLDivElement>(null);

  const detailQuery = useQuery({
    queryKey: ["backtest", selectedRunId],
    queryFn: () => platformApi.backtest(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => {
      const run = query.state.data as BacktestRun | undefined;
      return run && ACTIVE_STATUSES.has(run.status) ? 2000 : false;
    },
  });
  const contractsQuery = useQuery({ queryKey: ["backtest-contract-center"], queryFn: () => listContracts(), staleTime: 60_000 });
  const specsQuery = useQuery({ queryKey: ["contracts"], queryFn: platformApi.contracts, staleTime: 60_000 });
  const symbolGroupsQuery = useQuery({ queryKey: ["backtest-symbol-groups"], queryFn: platformApi.backtestSymbolGroups, staleTime: 60_000 });

  useEffect(() => {
    setChartSymbol("");
    setChartTimeframes([]);
    setSelectedOrder(null);
    setEquityRuleKey("");
    setChartRuleKey("");
    setPage(1);
    setFilters({ symbol: "", timeframe: "", rule_key: "", exit_reason: "" });
  }, [selectedRunId]);

  const detail = detailQuery.data;

  const ruleCatalog = useMemo(() => [...BUILTIN_RULES, ...customRules], [customRules]);
  const configuredSymbols = useMemo(() => new Set((specsQuery.data || []).filter((item) => item.enabled).map((item) => item.symbol.toLowerCase())), [specsQuery.data]);
  const priceDigitsBySymbol = useMemo(() => {
    const digits = new Map<string, number>();
    for (const spec of specsQuery.data || []) {
      const displayDigits = priceDigitsForTick(spec.price_tick);
      for (const key of contractSymbolKeys(spec.symbol)) digits.set(key, displayDigits);
    }
    return digits;
  }, [specsQuery.data]);
  const orderPrice = (symbol: string, value: unknown) => {
    const digits = [...contractSymbolKeys(symbol)]
      .map((key) => priceDigitsBySymbol.get(key))
      .find((item) => item !== undefined) ?? 4;
    return number(value, digits);
  };
  const symbolOptions = useMemo(() => (contractsQuery.data || []).map((item) => ({
    value: item.symbol,
    searchText: `${item.symbol} ${item.name}`.toLowerCase(),
    label: <span className="backtest-symbol-option"><strong>{item.symbol}</strong><span>{item.name}</span>{configuredSymbols.has(item.symbol.toLowerCase()) ? <Tag color="success">含成本</Tag> : <Tag>仅R指标</Tag>}</span>,
  })), [contractsQuery.data, configuredSymbols]);
  const selectedSymbolGroup = useMemo(
    () => (symbolGroupsQuery.data || []).find((group) => group.id === selectedSymbolGroupId),
    [selectedSymbolGroupId, symbolGroupsQuery.data],
  );

  const createMutation = useMutation({
    mutationFn: platformApi.createBacktest,
    onSuccess: (run) => {
      api.success("回测任务已提交");
      setActiveTab("overview");
      void queryClient.invalidateQueries({ queryKey: ["backtests"] });
      navigate(`/analysis/backtest/${run.id}`);
    },
    onError: (error: Error) => api.error(error.message),
  });
  const cancelMutation = useMutation({ mutationFn: platformApi.cancelBacktest, onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["backtests"] }) });
  const createSymbolGroupMutation = useMutation({
    mutationFn: platformApi.createBacktestSymbolGroup,
    onSuccess: (group) => {
      api.success("品种分组已保存");
      setSelectedSymbolGroupId(group.id);
      setSymbolGroupModalOpen(false);
      setSymbolGroupName("");
      void queryClient.invalidateQueries({ queryKey: ["backtest-symbol-groups"] });
    },
    onError: (error: Error) => api.error(error.message),
  });
  const deleteSymbolGroupMutation = useMutation({
    mutationFn: platformApi.deleteBacktestSymbolGroup,
    onSuccess: () => {
      api.success("品种分组已删除");
      setSelectedSymbolGroupId(undefined);
      void queryClient.invalidateQueries({ queryKey: ["backtest-symbol-groups"] });
    },
    onError: (error: Error) => api.error(error.message),
  });

  const orderParams = useMemo(() => {
    const params = new URLSearchParams({ page: String(page), page_size: "50" });
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params;
  }, [filters, page]);
  const ordersQuery = useQuery({
    queryKey: ["backtest-orders", selectedRunId, orderParams.toString()],
    queryFn: () => platformApi.backtestOrders(selectedRunId!, orderParams),
    enabled: Boolean(selectedRunId) && Boolean(detail?.summaries?.length),
  });
  useEffect(() => {
    const tableContent = orderTableRef.current?.querySelector<HTMLElement>(".ant-table-content");
    const topScroll = topOrderScrollRef.current;
    if (!tableContent || !topScroll) return;
    const syncTopScroll = () => { topScroll.scrollLeft = tableContent.scrollLeft; };
    syncTopScroll();
    tableContent.addEventListener("scroll", syncTopScroll);
    return () => tableContent.removeEventListener("scroll", syncTopScroll);
  }, [ordersQuery.data, ordersQuery.isLoading]);
  function submit(values: Record<string, unknown>) {
    const selectedKeys = values.rule_keys as string[];
    const maxHoldingBars = values.max_holding_bars == null ? undefined : Number(values.max_holding_bars);
    const payload: BacktestRequest = {
      name: String(values.name || ""),
      symbols: values.symbols as string[],
      timeframes: values.timeframes as string[],
      kline_count: Number(values.kline_count),
      ...(maxHoldingBars === undefined ? {} : { max_holding_bars: maxHoldingBars }),
      initial_capital: Number(values.initial_capital),
      single_symbol_position_pct: Number(values.single_symbol_position_pct),
      entry_conditions: values.entry_conditions as BacktestRequest["entry_conditions"],
      other_entry_conditions: values.other_entry_conditions as BacktestRequest["other_entry_conditions"],
      min_pattern_score: Number(values.min_pattern_score || 0),
      min_trend_score: Number(values.min_trend_score || 0),
      other_min_pattern_score: Number(values.other_min_pattern_score ?? 80),
      other_max_trend_score: Number(values.other_max_trend_score ?? 35),
      stop_loss_qtr_multiplier: Number(values.stop_loss_qtr_multiplier ?? 0.5),
      take_profit_rules: ruleCatalog.filter((rule) => selectedKeys.includes(rule.key)),
    };
    createMutation.mutate(payload);
  }

  function addCustomRule() {
    const multiplier = Number(customDraft.multiplier);
    if (!Number.isFinite(multiplier) || multiplier <= 0) return;
    const key = `custom-${customDraft.type.toLowerCase()}-${String(multiplier).replace(".", "_")}-${Date.now()}`;
    const rule: BacktestRule = { key, label: `${multiplier}${customDraft.type === "RR" ? "R" : " QTR"}`, type: customDraft.type, multiplier };
    setCustomRules((items) => [...items, rule]);
    const selected = form.getFieldValue("rule_keys") as string[];
    form.setFieldValue("rule_keys", [...selected, key]);
  }

  function syncOrderTableScroll(event: UIEvent<HTMLDivElement>) {
    const tableContent = orderTableRef.current?.querySelector<HTMLElement>(".ant-table-content");
    if (tableContent) tableContent.scrollLeft = event.currentTarget.scrollLeft;
  }

  function applySymbolGroup(groupId: string | undefined) {
    setSelectedSymbolGroupId(groupId);
    const group = (symbolGroupsQuery.data || []).find((item) => item.id === groupId);
    if (group) form.setFieldValue("symbols", group.symbols);
  }

  function openSymbolGroupModal() {
    const symbols = form.getFieldValue("symbols") as string[] | undefined;
    if (!symbols?.length) {
      api.warning("请先选择要保存的回测品种");
      return;
    }
    setSymbolGroupName("");
    setSymbolGroupModalOpen(true);
  }

  function saveSymbolGroup() {
    const symbols = form.getFieldValue("symbols") as string[] | undefined;
    if (!symbolGroupName.trim()) {
      api.warning("请输入分组名称");
      return;
    }
    if (!symbols?.length) return;
    createSymbolGroupMutation.mutate({ name: symbolGroupName, symbols });
  }

  function openOrder(order: BacktestOrder) {
    setSelectedOrder(order);
    setChartSymbol(order.symbol);
    setChartTimeframes([order.timeframe]);
    setChartRuleKey("");
    setActiveTab("chart");
  }

  const summaries = detail?.summaries || [];
  const best = summaries[0];
  const selectedEquityRuleKey = equityRuleKey || best?.rule_key || "";
  const selectedChartRuleKey = chartRuleKey;
  const selectedChartSymbol = chartSymbol;
  const chartProductMarkets = (detail?.markets || []).filter((item) => item.symbol === selectedChartSymbol);
  const chartMarkets = (detail?.markets || [])
    .filter((item) => item.symbol === selectedChartSymbol && chartTimeframes.includes(item.timeframe))
    .sort((left, right) => TIMEFRAMES.findIndex((item) => item.value === left.timeframe) - TIMEFRAMES.findIndex((item) => item.value === right.timeframe));
  const chartSymbolOptions = (detail?.markets || [])
    .map((item) => item.symbol)
    .filter((symbol, index, items) => items.indexOf(symbol) === index)
    .map((symbol) => ({ value: symbol, label: symbol }));
  const chartTimeframeOptions = TIMEFRAMES.filter((item) => chartProductMarkets.some((market) => market.timeframe === item.value));
  const equityCurveQuery = useQuery({
    queryKey: ["backtest-equity-curve", selectedRunId, selectedEquityRuleKey],
    queryFn: () => platformApi.backtestEquityCurve(selectedRunId!, selectedEquityRuleKey),
    enabled: Boolean(selectedRunId && selectedEquityRuleKey),
    staleTime: Infinity,
  });
  const overviewColumns: ColumnsType<BacktestSummary> = [
    { title: "止盈条件", dataIndex: "rule_label", fixed: "left", width: 132, render: (value) => <strong>{value}</strong> },
    { title: "样本", dataIndex: "sample_count", width: 72 },
    { title: "胜/负", width: 90, render: (_, row) => <span><b className="profit">{row.wins}</b> / <b className="loss">{row.losses}</b></span> },
    { title: "胜率", dataIndex: "win_rate", width: 88, render: (value) => <strong>{number(Number(value) * 100, 1)}%</strong> },
    { title: "止盈率", width: 88, render: (_, row) => `${number(row.sample_count ? row.take_profit_hits * 100 / row.sample_count : 0, 1)}%` },
    { title: "净收益", dataIndex: "net_pnl", align: "right", width: 110, render: (value) => value == null ? <Tooltip title="部分或全部品种未配置合约成本参数">--</Tooltip> : <span className={Number(value) >= 0 ? "profit" : "loss"}>{number(value)}</span> },
    { title: "平均R", dataIndex: "avg_r", align: "right", width: 88, render: (value) => number(value, 3) },
    { title: "累计R", dataIndex: "total_r", align: "right", width: 88, render: (value) => number(value, 2) },
    { title: <span className="backtest-column-title">收益因子<Tooltip title="收益因子 = 所有盈利的总和 ÷ 所有亏损的绝对值"><CircleHelp className="backtest-column-help" size={14} /></Tooltip></span>, dataIndex: "profit_factor", align: "right", width: 96, render: (value) => number(value, 2) },
    { title: "平均持有", dataIndex: "avg_holding_bars", align: "right", width: 100, render: (value) => `${number(value, 1)} 根` },
    { title: "不完整", dataIndex: "incomplete", width: 78 },
  ];
  const orderColumns: ColumnsType<BacktestOrder> = [
    { title: "品种", dataIndex: "symbol", width: 92, render: (value) => <strong className="symbol-cell">{value}</strong> },
    { title: "周期", dataIndex: "timeframe", width: 56 },
    { title: "止盈条件", dataIndex: "rule_label", width: 112 },
    { title: "方向", dataIndex: "direction", width: 132, render: (value, row) => <Tag color={value === "LONG" ? "red" : "green"}>{directionLabel(row)}</Tag> },
    { title: "结果", width: 64, render: (_, row) => exitTag(row.exit_reason, row.status) },
    { title: "手数", dataIndex: "quantity", align: "right", width: 56, render: (value, row) => row.cost_available ? value : "--" },
    { title: "进场", dataIndex: "entry_price", align: "right", width: 78, render: (value, row) => orderPrice(row.symbol, value) },
    { title: "出场", dataIndex: "exit_price", align: "right", width: 78, render: (value, row) => orderPrice(row.symbol, value) },
    { title: "净收益", dataIndex: "net_pnl", align: "right", width: 96, render: (value) => value == null ? "--" : <span className={Number(value) >= 0 ? "profit" : "loss"}>{number(value)}</span> },
    { title: "手续费", dataIndex: "fees", align: "right", width: 84, render: (value) => value == null ? "--" : number(value) },
    { title: "R", dataIndex: "r_multiple", align: "right", width: 52, render: (value) => number(value, 2) },
    { title: "持有", dataIndex: "holding_bars", width: 64, render: (value) => `${value}根` },
    { title: "进场时间", dataIndex: "entry_time", width: 144, render: (value) => formatMarketDateTime(value) },
    { title: "出场时间", dataIndex: "exit_time", width: 144, render: (value) => formatMarketDateTime(value) },
    { title: "", width: 38, render: (_, row) => <Tooltip title="查看K线"><Button type="text" icon={<Eye size={16} />} onClick={() => openOrder(row)} /></Tooltip> },
  ];

  const overview = detail ? <div className="backtest-overview">
    {detail.errors?.length ? <Alert type="warning" showIcon title={`${detail.errors.length} 个品种周期组合执行失败`} description={detail.errors.map((item) => `${item.symbol}/${item.timeframe}: ${item.message}`).join("；")} /> : null}
    <Table rowKey="rule_key" size="small" columns={overviewColumns} dataSource={summaries} pagination={false} scroll={{ x: 1050 }} />
  </div> : <Empty />;

  const selectedOrderRisk = selectedOrder && selectedOrder.entry_price != null && selectedOrder.stop_price != null
    ? Math.abs(Number(selectedOrder.entry_price) - Number(selectedOrder.stop_price))
    : null;
  const selectedOrderDetails = selectedOrder ? [
    { label: "品种", value: selectedOrder.symbol },
    { label: "周期", value: selectedOrder.timeframe },
    { label: "方向", value: <Tag color={selectedOrder.direction === "LONG" ? "red" : "green"}>{directionLabel(selectedOrder)}</Tag> },
    { label: "结果", value: exitTag(selectedOrder.exit_reason, selectedOrder.status) },
    { label: "进场价", value: orderPrice(selectedOrder.symbol, selectedOrder.entry_price) },
    { label: "出场价", value: orderPrice(selectedOrder.symbol, selectedOrder.exit_price) },
    { label: "止损价", value: orderPrice(selectedOrder.symbol, selectedOrder.stop_price) },
    { label: "目标价", value: orderPrice(selectedOrder.symbol, selectedOrder.target_price) },
    { label: "手数", value: selectedOrder.cost_available ? `${selectedOrder.quantity} 手` : "--" },
    { label: "止盈条件", value: selectedOrder.rule_label },
    { label: "形态质量评分", value: number(selectedOrder.signal.pattern_score ?? selectedOrder.score, 0) },
    { label: "趋势评分", value: number(selectedOrder.signal.score, 0) },
    { label: "净收益", value: selectedOrder.net_pnl == null ? "--" : number(selectedOrder.net_pnl) },
    { label: "实际R", value: selectedOrder.r_multiple == null ? "--" : `${number(selectedOrder.r_multiple, 2)}R${selectedOrderRisk == null ? "" : ` (R=${orderPrice(selectedOrder.symbol, selectedOrderRisk)})`}` },
    { label: "持有", value: `${selectedOrder.holding_bars}根K线` },
    { label: "进场时间", value: formatMarketDateTime(selectedOrder.entry_time) },
    { label: "出场时间", value: formatMarketDateTime(selectedOrder.exit_time) },
  ] : [];

  const chartView = <div className="backtest-chart-view">
    <div className="backtest-view-toolbar">
      <Select allowClear value={selectedChartSymbol || undefined} placeholder="选择品种" onChange={(value) => { setChartSymbol(value || ""); setChartTimeframes([]); setSelectedOrder(null); }} options={chartSymbolOptions} />
      <Select mode="multiple" value={chartTimeframes} placeholder="选择周期" disabled={!selectedChartSymbol} onChange={(value) => { setChartTimeframes(value); setSelectedOrder(null); }} options={chartTimeframeOptions} />
      <Select allowClear value={selectedChartRuleKey || undefined} placeholder="选择止盈条件" onChange={(value) => { setChartRuleKey(value || ""); if (value) setSelectedOrder(null); }} options={summaries.map((item) => ({ value: item.rule_key, label: item.rule_label }))} />
      <span className="selected-order-note">{selectedChartRuleKey ? "所选止盈条件将同步展示在全部已选周期图中" : selectedOrder ? `${selectedOrder.rule_label} · 单笔订单` : "选择品种与周期后查看K线结构"}</span>
    </div>
    {chartMarkets.length ? <div className="backtest-chart-stack">
      {chartMarkets.map((market) => <BacktestChartPanel key={`${market.symbol}|${market.timeframe}`} runId={selectedRunId!} symbol={market.symbol} timeframe={market.timeframe} ruleKey={selectedChartRuleKey} selectedOrder={selectedOrder} />)}
    </div> : <Empty description="暂无K线结构" />}
    {selectedOrder ? <section className="backtest-chart-order-details" aria-label="当前订单详情">
      {selectedOrderDetails.map((item) => {
        const value = item.label === "净收益" ? selectedOrder.net_pnl : item.label === "实际R" ? selectedOrder.r_multiple : null;
        const tone = value == null ? "" : Number(value) > 0 ? "order-detail-positive" : Number(value) < 0 ? "order-detail-negative" : "";
        const isPrice = ["进场价", "出场价", "止损价", "目标价"].includes(item.label);
        return <div key={item.label} className={[item.label.includes("时间") ? "backtest-order-detail-time" : "", isPrice ? "backtest-order-detail-price" : ""].filter(Boolean).join(" ")}><span>{item.label}</span><strong className={[item.label.includes("时间") ? "order-detail-time" : "", tone].filter(Boolean).join(" ")}>{item.value}</strong></div>;
      })}
    </section> : null}
  </div>;

  const ordersView = <div className="backtest-orders-view">
    <div className="backtest-view-toolbar backtest-filter-toolbar">
      <Select allowClear placeholder="品种" value={filters.symbol || undefined} onChange={(value) => { setPage(1); setFilters((item) => ({ ...item, symbol: value || "" })); }} options={(detail?.markets || []).map((item) => ({ value: item.symbol, label: item.symbol })).filter((item, index, all) => all.findIndex((other) => other.value === item.value) === index)} />
      <Select allowClear placeholder="周期" value={filters.timeframe || undefined} onChange={(value) => { setPage(1); setFilters((item) => ({ ...item, timeframe: value || "" })); }} options={TIMEFRAMES} />
      <Select allowClear placeholder="止盈条件" value={filters.rule_key || undefined} onChange={(value) => { setPage(1); setFilters((item) => ({ ...item, rule_key: value || "" })); }} options={summaries.map((item) => ({ value: item.rule_key, label: item.rule_label }))} />
      <Select allowClear placeholder="退出原因" value={filters.exit_reason || undefined} onChange={(value) => { setPage(1); setFilters((item) => ({ ...item, exit_reason: value || "" })); }} options={[{ value: "TAKE_PROFIT", label: "止盈" }, { value: "STOP_LOSS", label: "止损" }, { value: "TIME_EXIT", label: "到期平仓" }]} />
      <Button icon={<RotateCcw size={15} />} onClick={() => { setPage(1); setFilters({ symbol: "", timeframe: "", rule_key: "", exit_reason: "" }); }}>重置</Button>
    </div>
    <div className="backtest-orders-top-scroll" ref={topOrderScrollRef} onScroll={syncOrderTableScroll}><div /></div>
    <div className="backtest-orders-table" ref={orderTableRef}>
      <Table
        rowKey="id"
        size="small"
        tableLayout="fixed"
        columns={orderColumns}
        dataSource={ordersQuery.data?.items || []}
        loading={ordersQuery.isLoading}
        scroll={{ x: 1290 }}
        pagination={{ current: page, pageSize: 50, total: ordersQuery.data?.total || 0, showSizeChanger: false, onChange: setPage }}
        summary={() => {
          const totals = ordersQuery.data?.totals;
          return <Table.Summary.Row className="backtest-orders-total-row">
            <Table.Summary.Cell index={0} colSpan={8}>合计</Table.Summary.Cell>
            <Table.Summary.Cell index={8} align="right"><span className={Number(totals?.net_pnl || 0) >= 0 ? "profit" : "loss"}>{number(totals?.net_pnl)}</span></Table.Summary.Cell>
            <Table.Summary.Cell index={9} align="right">{number(totals?.fees)}</Table.Summary.Cell>
            <Table.Summary.Cell index={10} colSpan={5} />
          </Table.Summary.Row>;
        }}
        onRow={(row) => ({ onDoubleClick: () => openOrder(row) })}
      />
    </div>
  </div>;

  const equityView = <div className="backtest-equity-view">
    <div className="backtest-view-toolbar">
      <Select
        value={selectedEquityRuleKey || undefined}
        placeholder="选择止盈条件"
        onChange={setEquityRuleKey}
        options={summaries.map((item) => ({ value: item.rule_key, label: item.rule_label }))}
      />
    </div>
    {equityCurveQuery.isLoading ? <div className="backtest-chart-loading"><Spin /></div> : equityCurveQuery.isError ? <Alert type="error" showIcon title="收益图加载失败" description="请确认后端服务已重启后重试。" action={<Button size="small" onClick={() => void equityCurveQuery.refetch()}>重试</Button>} /> : equityCurveQuery.data?.items.length ? <BacktestEquityChart curve={equityCurveQuery.data} /> : <Empty description="所选止盈条件暂无已平仓订单" />}
  </div>;

  return <div className="backtest-page">
    {contextHolder}
    <Modal
      title="保存品种分组"
      open={symbolGroupModalOpen}
      okText="保存"
      cancelText="取消"
      confirmLoading={createSymbolGroupMutation.isPending}
      onOk={saveSymbolGroup}
      onCancel={() => setSymbolGroupModalOpen(false)}
    >
      <Input
        autoFocus
        maxLength={80}
        placeholder="例如：黑色系品种"
        value={symbolGroupName}
        onChange={(event) => setSymbolGroupName(event.target.value)}
        onPressEnter={saveSymbolGroup}
      />
    </Modal>
    <header className="page-heading backtest-heading">
      <div><span className="page-kicker">STRATEGY LAB</span><Typography.Title level={2}>{selectedRunId ? "头肩形态策略回测" : "添加策略回测"}</Typography.Title><Typography.Text>同一批信号独立比较止盈条件，结果按个人账户持久保存。</Typography.Text></div>
      <Space>
        <Button icon={<ArrowLeft size={17} />} onClick={() => navigate("/analysis/backtest")}>回测记录</Button>
        {detail && !ACTIVE_STATUSES.has(detail.status) ? <Button icon={<Download size={17} />} onClick={() => void downloadBacktest(detail.id)}>导出Excel</Button> : null}
      </Space>
    </header>
    <div className="backtest-workbench">
      <aside className="backtest-config">
        <div className="backtest-panel-title"><Play size={17} /><div><strong>本次回测</strong><span>品种、周期与退出规则</span></div></div>
        <Form form={form} layout="vertical" onFinish={submit} initialValues={{ name: "", symbols: [], timeframes: ["3m", "5m"], kline_count: 1000, initial_capital: 1000000, single_symbol_position_pct: 10, entry_conditions: ENTRY_CONDITIONS.map((item) => item.value), other_entry_conditions: OTHER_ENTRY_CONDITIONS.map((item) => item.value), min_pattern_score: 75, min_trend_score: 65, other_min_pattern_score: 80, other_max_trend_score: 35, stop_loss_qtr_multiplier: 0.5, rule_keys: DEFAULT_RULE_KEYS }}>
          <Form.Item name="name" label="回测名称"><Input placeholder="留空自动按时间命名" /></Form.Item>
          <Form.Item label="品种分组" className="backtest-symbol-group-item">
            <div className="backtest-symbol-group-picker">
              <Select
                allowClear
                value={selectedSymbolGroupId}
                placeholder="选择已保存的分组"
                loading={symbolGroupsQuery.isLoading}
                onChange={applySymbolGroup}
                options={(symbolGroupsQuery.data || []).map((group) => ({ value: group.id, label: `${group.name} (${group.symbols.length})` }))}
              />
              <Tooltip title="将当前选中的品种保存为新分组"><Button htmlType="button" icon={<Save size={16} />} onClick={openSymbolGroupModal} /></Tooltip>
              {selectedSymbolGroup ? <Popconfirm title={`删除分组“${selectedSymbolGroup.name}”？`} okText="删除" cancelText="取消" onConfirm={() => deleteSymbolGroupMutation.mutate(selectedSymbolGroup.id)}>
                <Button htmlType="button" danger icon={<Trash2 size={16} />} loading={deleteSymbolGroupMutation.isPending} />
              </Popconfirm> : null}
            </div>
          </Form.Item>
          <Form.Item name="symbols" label="回测品种" rules={[{ required: true, message: "至少选择一个品种" }]}><Select mode="multiple" showSearch maxTagCount="responsive" placeholder="搜索并加入本次回测" options={symbolOptions} optionFilterProp="searchText" /></Form.Item>
          <Form.Item name="timeframes" label="回测周期" rules={[{ required: true, message: "至少选择一个回测周期" }]}><Select mode="multiple" maxTagCount="responsive" placeholder="选择回测周期" options={TIMEFRAMES} /></Form.Item>
          <div className="backtest-number-grid">
            <Form.Item name="kline_count" label="回测K线数" rules={[{ required: true }]}><InputNumber min={120} max={8000} step={120} /></Form.Item>
            <Form.Item className="backtest-max-holding-item" name="max_holding_bars" label="最大持有K线（选填）"><InputNumber min={1} max={500} placeholder="不限制" /></Form.Item>
          </div>
          <div className="backtest-number-grid">
            <Form.Item name="initial_capital" label="初始资金" rules={[{ required: true }]}><InputNumber min={1} max={1000000000} step={10000} precision={2} addonBefore="¥" /></Form.Item>
            <Form.Item name="single_symbol_position_pct" label="单品种仓位" rules={[{ required: true }]}><InputNumber min={0.01} max={100} step={0.1} precision={2} addonAfter="%" /></Form.Item>
          </div>
          <section className="backtest-entry-section">
            <Form.Item name="entry_conditions" label="进场形态" dependencies={["other_entry_conditions"]} rules={[({ getFieldValue }) => ({ validator: (_, value: string[]) => value?.length || getFieldValue("other_entry_conditions")?.length ? Promise.resolve() : Promise.reject(new Error("至少选择一个进场形态")) })]}><Checkbox.Group className="backtest-check-grid two" options={ENTRY_CONDITIONS} /></Form.Item>
            <div className="backtest-number-grid backtest-score-grid">
              <Form.Item name="min_pattern_score" label="进场形态质量评分 ≥"><InputNumber min={0} max={100} step={1} /></Form.Item>
              <Form.Item name="min_trend_score" label="进场趋势评分 ≥"><InputNumber min={0} max={100} step={1} /></Form.Item>
            </div>
          </section>
          <section className="backtest-entry-section">
            <Form.Item name="other_entry_conditions" label="其他进场形态"><Checkbox.Group className="backtest-check-grid two" options={OTHER_ENTRY_CONDITIONS} /></Form.Item>
            <div className="backtest-number-grid backtest-score-grid">
              <Form.Item name="other_min_pattern_score" label="形态质量评分 ≥"><InputNumber min={0} max={100} step={1} /></Form.Item>
              <Form.Item name="other_max_trend_score" label="趋势评分 ≤"><InputNumber min={0} max={100} step={1} /></Form.Item>
            </div>
          </section>
          <Form.Item name="stop_loss_qtr_multiplier" label="止损条件（QTR 倍数）" rules={[{ required: true }]}><InputNumber min={0.1} max={20} step={0.1} /></Form.Item>
          <Form.Item name="rule_keys" label="止盈条件" rules={[{ required: true, message: "至少勾选一个止盈条件" }]}>
            <Checkbox.Group className="backtest-rule-grid">
              {ruleCatalog.map((rule) => <Checkbox key={rule.key} value={rule.key} className={rule.type === "PATTERN_TARGET" ? "pattern-target-rule" : undefined}>
                <span className="rule-label-with-help">
                  <span>{rule.label}</span>
                  {rule.type === "PATTERN_TARGET" ? <Tooltip
                    trigger="click"
                    title={<div className="pattern-target-help">头肩顶做空：量出“头部到颈线”的垂直高度 H，从跌破时的颈线位置向下投射 H，得到目标价。<br /><br />反向头肩做多：同样量出高度 H，从突破时的颈线位置向上投射 H，得到目标价。</div>}
                  ><Button type="text" size="small" className="rule-help" htmlType="button" aria-label="查看形态量度目标说明" icon={<CircleHelp size={15} />} onClick={(event) => { event.preventDefault(); event.stopPropagation(); }} /></Tooltip> : null}
                </span>
                {rule.key.startsWith("custom-") ? <button type="button" className="rule-remove" aria-label="删除条件" onClick={(event) => { event.preventDefault(); setCustomRules((items) => items.filter((item) => item.key !== rule.key)); }}>×</button> : null}
              </Checkbox>)}
            </Checkbox.Group>
          </Form.Item>
          <div className="custom-rule-row">
            <Select value={customDraft.type} onChange={(value) => setCustomDraft((item) => ({ ...item, type: value }))} options={[{ value: "RR", label: "R" }, { value: "QTR", label: "QTR" }]} />
            <InputNumber min={0.1} max={20} step={0.1} value={customDraft.multiplier} onChange={(value) => setCustomDraft((item) => ({ ...item, multiplier: Number(value) || 1 }))} />
            <Tooltip title="添加止盈条件"><Button icon={<Plus size={16} />} onClick={addCustomRule} /></Tooltip>
          </div>
          <Button block type="primary" htmlType="submit" icon={<Play size={16} />} loading={createMutation.isPending}>开始策略回测</Button>
        </Form>
      </aside>

      <main className="backtest-results">
        {detail ? <>
          <div className="backtest-run-strip">
            <div><strong>{detail.name}</strong><span>{dateTime(detail.created_at)} · {detail.request.symbols.length}个品种 / {detail.request.timeframes.length}个周期</span></div>
            <div className="backtest-run-progress">{statusTag(detail.status)}<Progress percent={detail.progress} size="small" showInfo={ACTIVE_STATUSES.has(detail.status)} />{ACTIVE_STATUSES.has(detail.status) ? <Button size="small" danger icon={<X size={14} />} onClick={() => cancelMutation.mutate(detail.id)}>取消</Button> : null}</div>
          </div>
          <section className="backtest-stat-band">
            <Statistic title="识别信号" value={detail.signal_count} />
            <Statistic title="虚拟订单" value={detail.order_count} />
            <Statistic title="最佳止盈条件" value={best?.rule_label || "--"} />
            <Statistic title="最佳胜率" value={best ? Number(best.win_rate) * 100 : 0} precision={1} suffix="%" />
            <Statistic title="累计R" value={best ? Number(best.total_r) : 0} precision={2} styles={{ content: { color: Number(best?.total_r || 0) >= 0 ? "#b33a3a" : "#16805b" } }} />
            <Statistic title="净收益" value={best?.net_pnl == null ? "--" : Number(best.net_pnl)} precision={2} prefix={best?.net_pnl == null ? undefined : "¥"} styles={{ content: { color: best?.net_pnl == null ? "#241f1e" : Number(best.net_pnl) >= 0 ? "#b33a3a" : "#16805b" } }} />
          </section>
          {ACTIVE_STATUSES.has(detail.status) && !summaries.length ? <div className="backtest-running"><Spin /><strong>后台正在扫描K线并生成独立止盈样本</strong><span>可以离开此页面，任务会继续运行。</span></div> : <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
            { key: "overview", label: <span><BarChart3 size={15} />止盈对比</span>, children: overview },
            { key: "chart", label: <span><BarChart3 size={15} />K线结构</span>, children: chartView },
            { key: "orders", label: <span><ListChecks size={15} />订单详情</span>, children: ordersView },
            { key: "equity", label: <span><BarChart3 size={15} />收益图</span>, children: equityView },
          ]} />}
        </> : <div className="backtest-empty">{detailQuery.isLoading ? <><Spin /><strong>正在加载回测详情</strong></> : <Empty description="配置左侧参数并开始策略回测" />}</div>}
      </main>
    </div>
  </div>;
}
