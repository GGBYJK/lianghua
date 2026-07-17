import React, { createContext, lazy, Suspense, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  ConfigProvider,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  Activity,
  BarChart3,
  BookOpen,
  BriefcaseBusiness,
  ChevronRight,
  CircleDollarSign,
  Database,
  Eye,
  FileClock,
  LogOut,
  Menu as MenuIcon,
  Settings2,
  ShieldCheck,
  MessageSquareText,
  RefreshCw,
  Trash2,
  TrendingDown,
  TrendingUp,
  Upload,
  UserRoundCog,
  WalletCards,
  Zap,
} from "lucide-react";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { login, logout, platformApi, restoreSession } from "./api";
import { deleteAlertFeedback, listAlertFeedbacks, listContracts, refreshContracts, updateContracts } from "../api";
import type { AlertFeedback, ContractCenterItem, ContractCenterRefresh } from "../types";
import type { ContractSpec, LedgerEntry, PaperOrder, PlatformUser, PositionLot, ProductCatalogItem, TradeSignal } from "./types";
import { apiTimestampToMs, formatApiDateTime, formatMarketDateTime } from "../time";
import "./platform.css";


const AnalysisApp = lazy(() => import("../main").then((module) => ({ default: module.AnalysisApp })));
const BacktestPage = lazy(() => import("./BacktestPage"));
const BacktestHistoryPage = lazy(() => import("./BacktestHistoryPage"));
const AuthContext = createContext<{
  user: PlatformUser | null;
  loading: boolean;
  setUser: (user: PlatformUser | null) => void;
}>({ user: null, loading: true, setUser: () => undefined });

const platformTheme = {
  token: {
    colorPrimary: "#0c7a5a",
    colorInfo: "#1168a8",
    colorSuccess: "#0c7a5a",
    colorWarning: "#b7791f",
    colorError: "#c23b32",
    borderRadius: 6,
    fontFamily: '"IBM Plex Sans", "Noto Sans SC", sans-serif',
  },
};

function useAuth() {
  return useContext(AuthContext);
}

function money(value: number | string | null | undefined) {
  const number = Number(value ?? 0);
  return new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number);
}

function price(value: number | string | null | undefined) {
  if (value == null) return "--";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function editablePrice(value: number | string | null | undefined) {
  return value == null ? undefined : Number(Number(value).toFixed(4));
}

function formatTime(value: string | null | undefined) {
  return formatApiDateTime(value);
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    MANUAL: "人工",
    SIGNAL: "信号",
    AUTO_STOP: "自动止损",
    AUTO_TAKE_PROFIT: "自动止盈",
  };
  return labels[source] || source;
}

function directionTag(direction: "LONG" | "SHORT") {
  return direction === "LONG" ? <Tag color="green">开多</Tag> : <Tag color="red">开空</Tag>;
}

function PatternTag({ pattern }: { pattern: TradeSignal["pattern"] }) {
  return pattern === "head_shoulders_top" ? <Tag color="volcano">头肩顶</Tag> : <Tag color="cyan">倒头肩底</Tag>;
}

export function PlatformApp() {
  const [user, setUser] = useState<PlatformUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    restoreSession().then(setUser).finally(() => setLoading(false));
  }, []);

  return (
    <ConfigProvider theme={platformTheme}>
      <AuthContext.Provider value={{ user, loading, setUser }}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route element={<PlatformLayout />}>
              <Route index element={<SignalDesk />} />
              <Route path="positions" element={<PositionsPage />} />
              <Route path="orders" element={<OrdersPage />} />
              <Route path="ledger" element={<LedgerPage />} />
              <Route path="analysis" element={<Navigate to="/analysis/monitor" replace />} />
              <Route path="analysis/backtest" element={<Suspense fallback={<PageLoading />}><BacktestHistoryPage /></Suspense>} />
              <Route path="analysis/backtest/new" element={<Suspense fallback={<PageLoading />}><BacktestPage /></Suspense>} />
              <Route path="analysis/backtest/:runId" element={<Suspense fallback={<PageLoading />}><BacktestPage /></Suspense>} />
              <Route path="analysis/:section" element={<EmbeddedAnalysis />} />
              <Route path="settings" element={<Navigate to="/settings/feedback" replace />} />
              <Route path="settings/feedback" element={<FeedbackSettingsPage />} />
              <Route path="settings/contracts" element={<ContractCenterPage />} />
              <Route path="admin/users" element={<PermissionRoute permission="users.manage"><UsersPage /></PermissionRoute>} />
              <Route path="admin/contracts" element={<PermissionRoute permission="contracts.manage"><ContractsPage /></PermissionRoute>} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthContext.Provider>
    </ConfigProvider>
  );
}

function EmbeddedAnalysis() {
  const { section } = useParams();
  const page = section === "pool" || section === "research" || section === "monitor" ? section : null;
  if (!page) return <Navigate to="/analysis/monitor" replace />;
  return (
    <section className="analysis-embedded">
      <Suspense fallback={<PageLoading />}>
        <AnalysisApp page={page} hidePageNavigation hideHeaderActions />
      </Suspense>
    </section>
  );
}

function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <PageLoading />;
  return user ? <Outlet /> : <Navigate to="/login" state={{ from: location.pathname }} replace />;
}

function PermissionRoute({ permission, children }: { permission: string; children: React.ReactNode }) {
  const { user } = useAuth();
  return user?.permissions.includes(permission) ? children : <Navigate to="/" replace />;
}

function PageLoading() {
  return <div className="platform-loading"><Spin size="large" /><span>正在载入交易工作台</span></div>;
}

function LoginPage() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [api, contextHolder] = message.useMessage();
  const mutation = useMutation({
    mutationFn: (values: { username: string; password: string }) => login(values.username, values.password),
    onSuccess: (result) => {
      setUser(result.user);
      navigate("/", { replace: true });
    },
    onError: (error: Error) => api.error(error.message),
  });
  if (user) return <Navigate to="/" replace />;

  return (
    <main className="login-canvas">
      {contextHolder}
      <section className="login-identity">
        <div className="brand-mark"><Activity size={25} /></div>
        <div>
          <Typography.Title level={1}>形态模拟交易台</Typography.Title>
          <Typography.Text>Head & Shoulders Paper Desk</Typography.Text>
        </div>
      </section>
      <section className="login-form-panel">
        <div className="login-form-heading">
          <span>SECURE ACCESS</span>
          <Typography.Title level={2}>登录交易账户</Typography.Title>
        </div>
        <Form form={form} layout="vertical" onFinish={(values) => mutation.mutate(values)} initialValues={{ username: "admin" }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input size="large" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password size="large" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={mutation.isPending}>进入工作台</Button>
        </Form>
        <div className="login-footnote"><ShieldCheck size={15} /> 默认管理员首次登录后应立即修改初始化密码</div>
      </section>
    </main>
  );
}

function PlatformLayout() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const queryClient = useQueryClient();
  const settingsChildren = [
    { key: "/settings/feedback", icon: <MessageSquareText size={16} />, label: "反馈列表" },
    { key: "/settings/contracts", icon: <Database size={16} />, label: "合约中心" },
    ...(user?.permissions.includes("contracts.manage") ? [{ key: "/admin/contracts", icon: <Settings2 size={16} />, label: "交易品种参数" }] : []),
  ];
  const items = [
    { key: "/", icon: <BarChart3 size={18} />, label: "信号交易池" },
    { key: "/positions", icon: <BriefcaseBusiness size={18} />, label: "当前持仓" },
    { key: "/orders", icon: <FileClock size={18} />, label: "订单记录" },
    { key: "/ledger", icon: <BookOpen size={18} />, label: "资金流水" },
    {
      key: "analysis-group",
      icon: <Activity size={18} />,
      label: "头肩顶工作台",
      children: [
        { key: "/analysis/monitor", label: "实时监控信息" },
        { key: "/analysis/pool", label: "品种检测池" },
        { key: "/analysis/research", label: "回测研究" },
        { key: "/analysis/backtest", label: "策略回测" },
      ],
    },
    ...(user?.permissions.includes("users.manage") ? [{ key: "/admin/users", icon: <UserRoundCog size={18} />, label: "用户管理" }] : []),
    { key: "settings-group", icon: <Settings2 size={18} />, label: "系统配置", children: settingsChildren },
  ];
  const selected = location.pathname.startsWith("/analysis/backtest") ? "/analysis/backtest" : location.pathname;
  const defaultOpenKeys = [
    ...(location.pathname.startsWith("/analysis") ? ["analysis-group"] : []),
    ...(location.pathname.startsWith("/settings") || location.pathname === "/admin/contracts" ? ["settings-group"] : []),
  ];

  async function signOut() {
    await logout();
    queryClient.clear();
    setUser(null);
    navigate("/login", { replace: true });
  }

  return (
    <Layout className="platform-shell">
      <Layout.Sider width={228} collapsedWidth={68} collapsed={collapsed} className="platform-sider" trigger={null}>
        <div className="platform-brand"><Activity size={22} /><span>形态交易台</span></div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          defaultOpenKeys={defaultOpenKeys}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
        <div className="sider-user">
          <div className="user-avatar">{user?.display_name.slice(0, 1)}</div>
          <div className="sider-user-copy"><strong>{user?.display_name}</strong><span>{user?.role_name}</span></div>
          <Tooltip title="退出登录"><Button type="text" icon={<LogOut size={17} />} onClick={() => void signOut()} /></Tooltip>
        </div>
      </Layout.Sider>
      <Layout>
        <Layout.Header className="platform-header">
          <Button type="text" icon={<MenuIcon size={20} />} onClick={() => setCollapsed((value) => !value)} />
          <div className="market-mode"><span className="status-dot" /> 模拟交易环境</div>
          <div className="header-spacer" />
          <span className="header-clock">{new Date().toLocaleDateString("zh-CN")}</span>
        </Layout.Header>
        <Layout.Content className="platform-content"><Outlet /></Layout.Content>
      </Layout>
    </Layout>
  );
}

function AccountBand() {
  const query = useQuery({ queryKey: ["account"], queryFn: platformApi.account, refetchInterval: 2000 });
  const data = query.data;
  return (
    <section className="account-band">
      <Statistic title="账户权益" value={Number(data?.equity ?? 0)} precision={2} prefix="¥" />
      <Statistic title="可用资金" value={Number(data?.available_funds ?? 0)} precision={2} prefix="¥" />
      <Statistic title="占用保证金" value={Number(data?.used_margin ?? 0)} precision={2} prefix="¥" />
      <Statistic title="浮动盈亏" value={Number(data?.unrealized_pnl ?? 0)} precision={2} prefix="¥" valueStyle={{ color: Number(data?.unrealized_pnl ?? 0) >= 0 ? "#0c7a5a" : "#c23b32" }} />
      <Statistic title="累计手续费" value={Number(data?.total_fees ?? 0)} precision={2} prefix="¥" />
    </section>
  );
}

function SignalDesk() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [filter, setFilter] = useState({ keyword: "", direction: "ALL", status: "ACTIVE" });
  const [selectedSignal, setSelectedSignal] = useState<TradeSignal | null>(null);
  const [signalForm] = Form.useForm();
  const [manualOpen, setManualOpen] = useState(false);
  const [manualForm] = Form.useForm();
  const manualSymbol = Form.useWatch("symbol", manualForm) as string | undefined;
  const signalsQuery = useQuery({ queryKey: ["signals"], queryFn: platformApi.signals, refetchInterval: 15000 });
  const contractsQuery = useQuery({
    queryKey: ["contracts"],
    queryFn: platformApi.contracts,
    enabled: manualOpen,
    staleTime: 60_000,
  });
  const marketContractsQuery = useQuery({
    queryKey: ["contract-center"],
    queryFn: () => listContracts(),
    enabled: manualOpen,
    staleTime: 60_000,
  });
  const manualContractOptions = useMemo(() => {
    const contracts = new Map<string, { symbol: string; name: string }>();
    for (const contract of marketContractsQuery.data || []) {
      contracts.set(contract.symbol.toLowerCase(), { symbol: contract.symbol, name: contract.name });
    }
    for (const contract of contractsQuery.data || []) {
      if (contract.enabled) contracts.set(contract.symbol.toLowerCase(), { symbol: contract.symbol, name: contract.name });
    }
    return [...contracts.values()].map((contract) => ({
      value: contract.symbol.toLowerCase(),
      label: `${contract.symbol.toUpperCase()} · ${contract.name}`,
    }));
  }, [contractsQuery.data, marketContractsQuery.data]);
  const manualQuoteQuery = useQuery({
    queryKey: ["manual-quote", manualSymbol],
    queryFn: () => platformApi.quotes([manualSymbol!]),
    enabled: manualOpen && Boolean(manualSymbol),
    refetchInterval: 10_000,
  });
  const manualQuote = manualQuoteQuery.data?.[0];
  const canTrade = user?.permissions.includes("trade.execute") ?? false;

  const rows = useMemo(() => [...(signalsQuery.data || [])].sort((left, right) => {
    const leftTime = apiTimestampToMs(left.created_at);
    const rightTime = apiTimestampToMs(right.created_at);
    return rightTime - leftTime;
  }).filter((item) => {
    const keywordMatches = !filter.keyword || item.symbol.toLowerCase().includes(filter.keyword.toLowerCase());
    const directionMatches = filter.direction === "ALL" || item.direction === filter.direction;
    const statusMatches = filter.status === "ALL" || (filter.status === "ACTIVE" ? item.tradeable : !item.tradeable);
    return keywordMatches && directionMatches && statusMatches;
  }), [signalsQuery.data, filter]);

  const tradeMutation = useMutation({
    mutationFn: (values: { quantity: number; stop_price?: number; take_profit_price?: number; disable_take_profit?: boolean }) => platformApi.openSignal(selectedSignal!.id, {
      ...values,
      take_profit_price: values.disable_take_profit ? null : values.take_profit_price,
      idempotency_key: crypto.randomUUID(),
    }),
    onSuccess: () => {
      api.success("模拟开仓已成交");
      setSelectedSignal(null);
      void queryClient.invalidateQueries({ queryKey: ["account"] });
      void queryClient.invalidateQueries({ queryKey: ["positions"] });
      void queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
    onError: (error: Error) => api.error(error.message),
  });

  const manualMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => platformApi.openManual({ ...values, idempotency_key: crypto.randomUUID() }),
    onSuccess: () => {
      api.success("人工市价单已成交");
      setManualOpen(false);
      manualForm.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["account"] });
      void queryClient.invalidateQueries({ queryKey: ["positions"] });
    },
    onError: (error: Error) => api.error(error.message),
  });

  function openSignalTrade(signal: TradeSignal) {
    setSelectedSignal(signal);
    signalForm.setFieldsValue({
      quantity: 1,
      stop_price: editablePrice(signal.suggested_stop_price),
      take_profit_price: editablePrice(signal.suggested_take_profit_price),
      disable_take_profit: signal.suggested_take_profit_price == null,
    });
  }

  const columns: ColumnsType<TradeSignal> = [
    { title: "合约", dataIndex: "symbol", width: 105, render: (value) => <strong className="symbol-cell">{String(value).toUpperCase()}</strong> },
    { title: "周期", dataIndex: "timeframe", width: 72 },
    { title: "形态", dataIndex: "pattern", width: 112, render: (value) => <PatternTag pattern={value} /> },
    { title: "方向", dataIndex: "direction", width: 84, render: directionTag },
    { title: "评分", width: 90, render: (_, row) => <span className="score-value">{row.signal_payload.pattern_score ?? row.score}</span> },
    { title: "建议入场", dataIndex: "suggested_entry_price", align: "right", render: price },
    { title: "止损", dataIndex: "suggested_stop_price", align: "right", render: price },
    { title: "目标", dataIndex: "suggested_target_price", align: "right", render: price },
    { title: "RR", dataIndex: "risk_reward_ratio", width: 72, align: "right", render: (value) => Number(value).toFixed(2) },
    { title: "检测时间", dataIndex: "created_at", width: 164, render: (value) => formatTime(value) },
    { title: "状态", width: 98, render: (_, row) => row.tradeable
      ? <Tag color="success">可交易</Tag>
      : <Tooltip title={row.tradeable_reason}><Tag color={row.tradeable_reason?.includes("行情") ? "warning" : undefined}>{row.tradeable_reason?.includes("行情") ? "行情过期" : "已失效"}</Tag></Tooltip> },
    { title: "操作", width: 150, render: (_, row) => <Space size={4}>
      <Button type="link" href={`/analysis?symbol=${encodeURIComponent(row.symbol)}&timeframe=${encodeURIComponent(row.timeframe)}`}>K线</Button>
      {canTrade && row.tradeable ? <Button type="primary" size="small" icon={<Zap size={14} />} onClick={() => openSignalTrade(row)}>交易</Button> : null}
    </Space> },
  ];

  return (
    <div className="desk-page">
      {contextHolder}
      <header className="page-heading">
        <div><span className="page-kicker">PAPER EXECUTION</span><Typography.Title level={2}>头肩形态交易池</Typography.Title></div>
        {canTrade ? <Button icon={<CircleDollarSign size={17} />} onClick={() => setManualOpen(true)}>人工开仓</Button> : <Tag>只读模式</Tag>}
      </header>
      <AccountBand />
      <section className="table-section">
        <div className="table-toolbar">
          <Input.Search allowClear placeholder="搜索合约" value={filter.keyword} onChange={(event) => setFilter((current) => ({ ...current, keyword: event.target.value }))} />
          <Select value={filter.direction} onChange={(value) => setFilter((current) => ({ ...current, direction: value }))} options={[{ value: "ALL", label: "全部方向" }, { value: "LONG", label: "开多" }, { value: "SHORT", label: "开空" }]} />
          <Select value={filter.status} onChange={(value) => setFilter((current) => ({ ...current, status: value }))} options={[{ value: "ACTIVE", label: "仅可交易" }, { value: "ALL", label: "全部信号" }, { value: "EXPIRED", label: "已失效" }]} />
          <span className="table-count">{rows.length} 条信号</span>
        </div>
        {signalsQuery.isError ? <Alert type="error" showIcon message={(signalsQuery.error as Error).message} /> : null}
        <Table rowKey="id" columns={columns} dataSource={rows} loading={signalsQuery.isLoading} scroll={{ x: 1220 }} pagination={{ pageSize: 20, showSizeChanger: false }} size="middle" />
      </section>

      <Modal title="确认信号开仓" open={Boolean(selectedSignal)} onCancel={() => setSelectedSignal(null)} onOk={() => signalForm.submit()} confirmLoading={tradeMutation.isPending} okText="确认模拟成交">
        {selectedSignal ? <div className="trade-ticket-summary">
          <div><span>合约</span><strong>{selectedSignal.symbol.toUpperCase()}</strong></div>
          <div><span>方向</span>{directionTag(selectedSignal.direction)}</div>
          <div><span>策略 RR</span><strong>{Number(selectedSignal.risk_reward_ratio).toFixed(2)}</strong></div>
        </div> : null}
        <Form form={signalForm} layout="vertical" onFinish={(values) => tradeMutation.mutate(values)}>
          <Form.Item name="quantity" label="手数" rules={[{ required: true }]}><InputNumber min={1} precision={0} className="full-input" /></Form.Item>
          <Form.Item name="stop_price" label="止损价" rules={[{ required: true, message: "信号交易必须设置止损价" }]}><InputNumber min={0} className="full-input" /></Form.Item>
          <Form.Item noStyle shouldUpdate={(previous, current) => previous.disable_take_profit !== current.disable_take_profit}>{({ getFieldValue }) => getFieldValue("disable_take_profit") ? null : <Form.Item name="take_profit_price" label="止盈价"><InputNumber min={0} className="full-input" /></Form.Item>}</Form.Item>
          <Form.Item name="disable_take_profit" valuePropName="checked"><Switch /> <span className="switch-label">暂不启用自动止盈</span></Form.Item>
        </Form>
      </Modal>

      <Modal title="人工市价开仓" open={manualOpen} onCancel={() => { setManualOpen(false); manualForm.resetFields(); }} onOk={() => manualForm.submit()} confirmLoading={manualMutation.isPending} okText="提交市价单">
        <Form form={manualForm} layout="vertical" onFinish={(values) => manualMutation.mutate(values)} initialValues={{ position_side: "LONG", quantity: 1 }}>
          <Form.Item name="symbol" label="合约代码" rules={[{ required: true }]}>
            <Select
              showSearch
              allowClear
              loading={contractsQuery.isLoading || marketContractsQuery.isLoading}
              placeholder="输入代码或名称搜索"
              filterOption={(input, option) => String(option?.label ?? "").toLowerCase().includes(input.trim().toLowerCase())}
              options={manualContractOptions}
            />
          </Form.Item>
          {manualSymbol ? <div className="manual-quote-strip">
            <div><span>最新价</span><strong>{manualQuoteQuery.isLoading ? <Spin size="small" /> : price(manualQuote?.last_price)}</strong></div>
            <div><span>更新时间</span><strong>{manualQuote?.market_time ? formatMarketDateTime(manualQuote.market_time) : formatTime(manualQuote?.updated_at)}</strong></div>
            <Tooltip title="每 10 秒刷新行情"><RefreshCw size={15} className={manualQuoteQuery.isFetching ? "is-spinning" : ""} /></Tooltip>
          </div> : null}
          {manualQuoteQuery.isError ? <Alert type="warning" showIcon message={(manualQuoteQuery.error as Error).message} /> : null}
          <Form.Item name="position_side" label="方向"><Select options={[{ value: "LONG", label: "开多" }, { value: "SHORT", label: "开空" }]} /></Form.Item>
          <Form.Item name="quantity" label="手数"><InputNumber min={1} precision={0} className="full-input" /></Form.Item>
          <div className="form-grid"><Form.Item name="stop_price" label="止损价"><InputNumber min={0} className="full-input" /></Form.Item><Form.Item name="take_profit_price" label="止盈价"><InputNumber min={0} className="full-input" /></Form.Item></div>
        </Form>
      </Modal>
    </div>
  );
}

function PositionsPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [editing, setEditing] = useState<PositionLot | null>(null);
  const [form] = Form.useForm();
  const query = useQuery({ queryKey: ["positions"], queryFn: platformApi.positions, refetchInterval: 2000 });
  const closeMutation = useMutation({
    mutationFn: (lot: PositionLot) => platformApi.closePosition(lot.id, lot.remaining_quantity),
    onSuccess: () => { api.success("持仓已平仓"); void queryClient.invalidateQueries(); },
    onError: (error: Error) => api.error(error.message),
  });
  const ruleMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => platformApi.updateExitRules(editing!.id, values),
    onSuccess: () => { api.success("退出规则已更新"); setEditing(null); void queryClient.invalidateQueries({ queryKey: ["positions"] }); },
    onError: (error: Error) => api.error(error.message),
  });
  const columns: ColumnsType<PositionLot> = [
    { title: "合约", dataIndex: "symbol", render: (value) => <strong>{String(value).toUpperCase()}</strong> },
    { title: "方向", dataIndex: "side", render: directionTag },
    { title: "持仓", dataIndex: "remaining_quantity" },
    { title: "开仓均价", dataIndex: "open_price", align: "right", render: price },
    { title: "最新价", dataIndex: "last_price", align: "right", render: price },
    { title: "保证金", dataIndex: "margin", align: "right", render: (value) => `¥${money(value)}` },
    { title: "浮动盈亏", dataIndex: "unrealized_pnl", align: "right", render: (value) => <span className={Number(value) >= 0 ? "profit" : "loss"}>{money(value)}</span> },
    { title: "止损", dataIndex: "stop_price", align: "right", render: price },
    { title: "止盈", dataIndex: "take_profit_price", align: "right", render: price },
    { title: "开仓时间", dataIndex: "opened_at", render: formatTime },
    { title: "操作", width: 160, render: (_, row) => <Space><Button size="small" onClick={() => { setEditing(row); form.setFieldsValue({ stop_price: row.stop_price ? Number(row.stop_price) : undefined, take_profit_price: row.take_profit_price ? Number(row.take_profit_price) : undefined }); }}>风控</Button><Button danger size="small" loading={closeMutation.isPending} onClick={() => Modal.confirm({ title: `平仓 ${row.symbol.toUpperCase()}？`, content: `将按最新行情平掉 ${row.remaining_quantity} 手。`, onOk: () => closeMutation.mutateAsync(row) })}>平仓</Button></Space> },
  ];
  return <div className="desk-page">{contextHolder}<PageTitle kicker="LIVE POSITIONS" title="当前持仓" /><AccountBand /><section className="table-section"><Table rowKey="id" columns={columns} dataSource={query.data || []} loading={query.isLoading} scroll={{ x: 1200 }} pagination={false} /></section><Modal title="修改止损止盈" open={Boolean(editing)} onCancel={() => setEditing(null)} onOk={() => form.submit()} confirmLoading={ruleMutation.isPending}><Form form={form} layout="vertical" onFinish={(values) => ruleMutation.mutate(values)}><Form.Item name="stop_price" label="止损价"><InputNumber min={0} className="full-input" /></Form.Item><Form.Item name="take_profit_price" label="止盈价"><InputNumber min={0} className="full-input" /></Form.Item></Form></Modal></div>;
}

function OrdersPage() {
  const query = useQuery({ queryKey: ["orders"], queryFn: platformApi.orders, refetchInterval: 3000 });
  const columns: ColumnsType<PaperOrder> = [
    { title: "时间", dataIndex: "created_at", width: 175, render: formatTime },
    { title: "合约", dataIndex: "symbol", render: (value) => <strong>{String(value).toUpperCase()}</strong> },
    { title: "持仓方向", dataIndex: "position_side", render: directionTag },
    { title: "动作", dataIndex: "position_effect", render: (value) => value === "OPEN" ? "开仓" : "平仓" },
    { title: "手数", dataIndex: "quantity" },
    { title: "成交价", dataIndex: "filled_price", align: "right", render: price },
    { title: "来源", dataIndex: "source", render: (value) => <Tag>{sourceLabel(value)}</Tag> },
    { title: "状态", dataIndex: "status", render: (value) => <Tag color={value === "FILLED" ? "success" : "default"}>{value}</Tag> },
    { title: "订单号", dataIndex: "id", render: (value) => <Typography.Text copyable={{ text: value }} className="order-id">{String(value).slice(0, 8)}</Typography.Text> },
  ];
  return <div className="desk-page"><PageTitle kicker="EXECUTION LOG" title="订单记录" /><section className="table-section"><Table rowKey="id" columns={columns} dataSource={query.data || []} loading={query.isLoading} pagination={{ pageSize: 30 }} /></section></div>;
}

function LedgerPage() {
  const query = useQuery({ queryKey: ["ledger"], queryFn: platformApi.ledger, refetchInterval: 5000 });
  const columns: ColumnsType<LedgerEntry> = [
    { title: "时间", dataIndex: "created_at", width: 180, render: formatTime },
    { title: "类型", dataIndex: "entry_type", render: (value) => <Tag>{value}</Tag> },
    { title: "说明", dataIndex: "description" },
    { title: "变动金额", dataIndex: "amount", align: "right", render: (value) => <span className={Number(value) >= 0 ? "profit" : "loss"}>{Number(value) >= 0 ? "+" : ""}{money(value)}</span> },
    { title: "资金余额", dataIndex: "balance_after", align: "right", render: money },
  ];
  return <div className="desk-page"><PageTitle kicker="ACCOUNT LEDGER" title="资金流水" /><AccountBand /><section className="table-section"><Table rowKey="id" columns={columns} dataSource={query.data || []} loading={query.isLoading} pagination={{ pageSize: 30 }} /></section></div>;
}

function FeedbackSettingsPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<AlertFeedback | null>(null);
  const query = useQuery({ queryKey: ["feedback-settings"], queryFn: () => listAlertFeedbacks(300) });
  const deleteMutation = useMutation({
    mutationFn: deleteAlertFeedback,
    onSuccess: () => {
      api.success("反馈记录已删除");
      setSelected(null);
      void queryClient.invalidateQueries({ queryKey: ["feedback-settings"] });
    },
    onError: (error: Error) => api.error(error.message),
  });
  const rows = useMemo(() => (query.data || []).filter((item) => {
    const term = keyword.trim().toLowerCase();
    return !term || item.symbol.toLowerCase().includes(term) || item.feedback_note.toLowerCase().includes(term);
  }), [query.data, keyword]);
  const columns: ColumnsType<AlertFeedback> = [
    { title: "合约", dataIndex: "symbol", width: 120, render: (value) => <strong className="symbol-cell">{String(value).toUpperCase()}</strong> },
    { title: "周期", dataIndex: "timeframe", width: 80 },
    { title: "形态", dataIndex: "pattern", width: 120, render: (value) => <PatternTag pattern={value} /> },
    { title: "评分", dataIndex: "score", width: 80, render: (value) => <span className="score-value">{value}</span> },
    { title: "反馈内容", dataIndex: "feedback_note", ellipsis: true },
    { title: "记录时间", dataIndex: "created_at", width: 180, render: formatTime },
    { title: "操作", width: 145, render: (_, row) => <Space size={4}>
      <Button type="text" icon={<Eye size={15} />} onClick={() => setSelected(row)}>查看</Button>
      <Button type="text" danger icon={<Trash2 size={15} />} onClick={() => Modal.confirm({ title: "删除反馈记录？", content: "删除后无法恢复。", okButtonProps: { danger: true }, onOk: () => deleteMutation.mutateAsync(row.id) })}>删除</Button>
    </Space> },
  ];
  return (
    <div className="desk-page">
      {contextHolder}
      <PageTitle kicker="SIGNAL FEEDBACK" title="反馈列表" />
      <section className="table-section">
        <div className="table-toolbar">
          <Input.Search allowClear placeholder="搜索合约或反馈内容" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
          <span className="table-count">{rows.length} 条反馈</span>
        </div>
        <Table rowKey="id" columns={columns} dataSource={rows} loading={query.isLoading} pagination={{ pageSize: 25 }} />
      </section>
      <Modal title="反馈详情" open={Boolean(selected)} onCancel={() => setSelected(null)} footer={<Button onClick={() => setSelected(null)}>关闭</Button>}>
        {selected ? (
          <div className="feedback-settings-detail">
            <div className="trade-ticket-summary">
              <div><span>合约</span><strong>{selected.symbol.toUpperCase()}</strong></div>
              <div><span>周期</span><strong>{selected.timeframe}</strong></div>
              <div><span>评分</span><strong>{selected.score}</strong></div>
            </div>
            <Typography.Title level={5}>反馈内容</Typography.Title>
            <p className="settings-note">{selected.feedback_note || "未填写反馈内容"}</p>
            <Typography.Title level={5}>原始信号</Typography.Title>
            <p className="settings-note muted">{selected.message}</p>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

function ContractCenterPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [exchange, setExchange] = useState("ALL");
  const [refreshState, setRefreshState] = useState<ContractCenterRefresh | null>(null);
  const query = useQuery({ queryKey: ["contract-center"], queryFn: () => listContracts() });
  const refreshMutation = useMutation({
    mutationFn: () => refreshContracts("SHFE,DCE,CZCE"),
    onSuccess: (result) => {
      setRefreshState(result);
      api.success(result.new_count || result.stale_count ? "已获取合约差异，请确认后应用" : "当前合约中心已是最新状态");
    },
    onError: (error: Error) => api.error(error.message),
  });
  const applyMutation = useMutation({
    mutationFn: () => updateContracts({
      symbols: refreshState?.new_symbols || [],
      latest_symbols: refreshState?.latest_symbols || [],
      exchanges: refreshState?.exchanges || [],
      prune_missing: true,
    }),
    onSuccess: (result) => {
      queryClient.setQueryData(["contract-center"], result.items);
      setRefreshState(null);
      api.success(`已新增 ${result.inserted} 个合约，移除 ${result.removed} 个失效合约`);
    },
    onError: (error: Error) => api.error(error.message),
  });
  const contracts = query.data || [];
  const rows = exchange === "ALL" ? contracts : contracts.filter((item) => item.exchange === exchange);
  const counts = contracts.reduce<Record<string, number>>((result, item) => {
    result[item.exchange] = (result[item.exchange] || 0) + 1;
    return result;
  }, {});
  const columns: ColumnsType<ContractCenterItem> = [
    { title: "合约代码", dataIndex: "symbol", render: (value) => <strong className="symbol-cell">{String(value).toUpperCase()}</strong> },
    { title: "交易所", dataIndex: "exchange", width: 120, render: (value) => <Tag>{value}</Tag> },
    { title: "名称", dataIndex: "name" },
    { title: "更新时间", dataIndex: "updated_at", width: 190, render: formatTime },
  ];
  const hasChanges = Boolean(refreshState && (refreshState.new_symbols.length || refreshState.stale_symbols.length));
  return (
    <div className="desk-page">
      {contextHolder}
      <PageTitle kicker="MARKET CONTRACTS" title="合约中心" action={<Space>
        <Button icon={<RefreshCw size={16} />} loading={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>获取最新合约</Button>
        <Button type="primary" disabled={!hasChanges} loading={applyMutation.isPending} onClick={() => applyMutation.mutate()}>确认应用更新</Button>
      </Space>} />
      <section className="settings-summary">
        <Statistic title="全部合约" value={contracts.length} />
        <Statistic title="SHFE" value={counts.SHFE || 0} />
        <Statistic title="DCE" value={counts.DCE || 0} />
        <Statistic title="CZCE" value={counts.CZCE || 0} />
        <Statistic title="待新增 / 待移除" value={`${refreshState?.new_count || 0} / ${refreshState?.stale_count || 0}`} />
      </section>
      {refreshState?.new_symbols.length ? <ContractDiffBand title="待新增合约" tone="new" symbols={refreshState.new_symbols} /> : null}
      {refreshState?.stale_symbols.length ? <ContractDiffBand title="待移除失效合约" tone="stale" symbols={refreshState.stale_symbols} /> : null}
      <section className="table-section">
        <div className="table-toolbar">
          <Select value={exchange} onChange={setExchange} options={[{ value: "ALL", label: "全部交易所" }, { value: "SHFE", label: "SHFE" }, { value: "DCE", label: "DCE" }, { value: "CZCE", label: "CZCE" }]} />
          <span className="table-count">{rows.length} 个合约</span>
        </div>
        <Table rowKey="id" columns={columns} dataSource={rows} loading={query.isLoading} pagination={{ pageSize: 30 }} />
      </section>
    </div>
  );
}

function ContractDiffBand({ title, tone, symbols }: { title: string; tone: "new" | "stale"; symbols: string[] }) {
  return (
    <section className={`contract-diff-band ${tone}`}>
      <strong>{title}</strong>
      <div>{symbols.slice(0, 80).map((symbol) => <Tag key={symbol}>{symbol}</Tag>)}{symbols.length > 80 ? <Tag>+{symbols.length - 80}</Tag> : null}</div>
    </section>
  );
}

function UsersPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const query = useQuery({ queryKey: ["admin-users"], queryFn: platformApi.users });
  const createMutation = useMutation({ mutationFn: platformApi.createUser, onSuccess: () => { api.success("用户已创建"); setOpen(false); form.resetFields(); void queryClient.invalidateQueries({ queryKey: ["admin-users"] }); }, onError: (error: Error) => api.error(error.message) });
  const statusMutation = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => platformApi.updateUser(id, { status }), onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin-users"] }), onError: (error: Error) => api.error(error.message) });
  const columns: ColumnsType<PlatformUser> = [
    { title: "用户", render: (_, row) => <div className="user-cell"><span>{row.display_name.slice(0, 1)}</span><div><strong>{row.display_name}</strong><small>{row.username}</small></div></div> },
    { title: "角色", dataIndex: "role_name", render: (value) => <Tag color={value === "管理员" ? "blue" : undefined}>{value}</Tag> },
    { title: "状态", dataIndex: "status", render: (value) => <Tag color={value === "ACTIVE" ? "success" : "default"}>{value === "ACTIVE" ? "正常" : "停用"}</Tag> },
    { title: "创建时间", dataIndex: "created_at", render: formatTime },
    { title: "操作", render: (_, row) => <Button size="small" danger={row.status === "ACTIVE"} onClick={() => statusMutation.mutate({ id: row.id, status: row.status === "ACTIVE" ? "DISABLED" : "ACTIVE" })}>{row.status === "ACTIVE" ? "停用" : "启用"}</Button> },
  ];
  return <div className="desk-page">{contextHolder}<PageTitle kicker="ACCESS CONTROL" title="用户与权限" action={<Button type="primary" onClick={() => setOpen(true)}>新增用户</Button>} /><section className="table-section"><Table rowKey="id" columns={columns} dataSource={query.data || []} loading={query.isLoading} pagination={false} /></section><Modal title="新增模拟交易用户" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={createMutation.isPending}><Form form={form} layout="vertical" initialValues={{ role: "TRADER", initial_balance: 1000000 }} onFinish={(values) => createMutation.mutate(values)}><Form.Item name="username" label="用户名" rules={[{ required: true }, { min: 3 }]}><Input /></Form.Item><Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="password" label="初始密码" rules={[{ required: true }, { min: 8 }]}><Input.Password /></Form.Item><Form.Item name="role" label="角色"><Select options={[{ value: "ADMIN", label: "管理员" }, { value: "TRADER", label: "交易员" }, { value: "VIEWER", label: "只读用户" }]} /></Form.Item><Form.Item name="initial_balance" label="初始资金"><InputNumber min={0} className="full-input" /></Form.Item></Form></Modal></div>;
}

function ContractsPage() {
  const queryClient = useQueryClient();
  const [api, contextHolder] = message.useMessage();
  const [editing, setEditing] = useState<ContractSpec | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const costFileInput = useRef<HTMLInputElement>(null);
  const feeMode = Form.useWatch("fee_mode", form) || "TURNOVER_RATE";
  const closeTodayFeeMode = Form.useWatch("fee_close_today_mode", form) as "TURNOVER_RATE" | "PER_LOT" | undefined;
  const selectedProduct = Form.useWatch("symbol", form) as string | undefined;
  const query = useQuery({ queryKey: ["contracts"], queryFn: platformApi.contracts });
  const productsQuery = useQuery({ queryKey: ["trading-products"], queryFn: platformApi.products });
  const selectedCatalogProduct = useMemo<ProductCatalogItem | undefined>(
    () => productsQuery.data?.find((item) => item.symbol === selectedProduct),
    [productsQuery.data, selectedProduct],
  );
  const productDetailsQuery = useQuery({
    queryKey: ["trading-product-details", selectedCatalogProduct?.representative_symbol],
    queryFn: () => platformApi.productDetails(selectedCatalogProduct!.representative_symbol),
    enabled: open && !editing && Boolean(selectedCatalogProduct),
  });
  useEffect(() => {
    if (!editing && productDetailsQuery.data) {
      const details = productDetailsQuery.data;
      form.setFieldsValue({
        exchange: details.exchange,
        name: details.name,
        multiplier: Number(details.multiplier),
        price_tick: Number(details.price_tick),
        margin_rate: details.margin_rate == null ? undefined : Number(details.margin_rate),
        fee_mode: details.fee_mode,
        fee_value: details.fee_value == null ? undefined : (details.fee_mode === "TURNOVER_RATE" ? Number(details.fee_value) * 100 : Number(details.fee_value)),
        fee_close_today_mode: details.fee_close_today_mode || undefined,
        fee_close_today_value: details.fee_close_today_value == null ? undefined : (details.fee_close_today_mode === "TURNOVER_RATE" ? Number(details.fee_close_today_value) * 100 : Number(details.fee_close_today_value)),
      });
    }
  }, [editing, form, productDetailsQuery.data]);
  const mutation = useMutation({ mutationFn: (values: Record<string, unknown>) => {
    const feeMode = String(values.fee_mode);
    const closeTodayFeeMode = values.fee_close_today_mode ? String(values.fee_close_today_mode) : null;
    return platformApi.saveContract(String(values.symbol), {
      ...values,
      fee_value: feeMode === "TURNOVER_RATE" ? Number(values.fee_value || 0) / 100 : values.fee_value,
      fee_close_today_mode: closeTodayFeeMode,
      fee_close_today_value: closeTodayFeeMode
        ? (closeTodayFeeMode === "TURNOVER_RATE" ? Number(values.fee_close_today_value || 0) / 100 : values.fee_close_today_value)
        : null,
    });
  }, onSuccess: () => { api.success("品种参数已保存"); setOpen(false); setEditing(null); form.resetFields(); void queryClient.invalidateQueries({ queryKey: ["contracts"] }); }, onError: (error: Error) => api.error(error.message) });
  const importMutation = useMutation({
    mutationFn: platformApi.importProductCosts,
    onSuccess: (result) => {
      api.success(`已导入 ${result.imported} 个品种的保证金及手续费`);
      if (result.errors.length) api.warning(`${result.errors.length} 行未能识别`);
      void queryClient.invalidateQueries({ queryKey: ["trading-product-details"] });
    },
    onError: (error: Error) => api.error(error.message),
  });
  function edit(row?: ContractSpec) {
    setEditing(row || null); setOpen(true);
    form.setFieldsValue(row ? {
      ...row,
      margin_rate: Number(row.margin_rate),
      multiplier: Number(row.multiplier),
      price_tick: Number(row.price_tick),
      fee_value: row.fee_mode === "TURNOVER_RATE" ? Number(row.fee_value) * 100 : Number(row.fee_value),
      fee_close_today_mode: row.fee_close_today_mode || undefined,
      fee_close_today_value: row.fee_close_today_value == null ? undefined : (row.fee_close_today_mode === "TURNOVER_RATE" ? Number(row.fee_close_today_value) * 100 : Number(row.fee_close_today_value)),
    } : { enabled: true, margin_rate: 0.12, multiplier: 1, price_tick: 1, fee_mode: "TURNOVER_RATE", fee_value: 0, fee_close_today_mode: undefined, fee_close_today_value: undefined });
  }
  const columns: ColumnsType<ContractSpec> = [
    { title: "品种", dataIndex: "symbol", render: (value) => <strong>{String(value).toUpperCase()}</strong> },
    { title: "交易所", dataIndex: "exchange" }, { title: "名称", dataIndex: "name" },
    { title: "合约乘数", dataIndex: "multiplier", align: "right", render: price },
    { title: "最小变动", dataIndex: "price_tick", align: "right", render: price },
    { title: "保证金率", dataIndex: "margin_rate", align: "right", render: (value) => `${(Number(value) * 100).toFixed(2)}%` },
    { title: "手续费", render: (_, row) => row.fee_mode === "TURNOVER_RATE" ? `${(Number(row.fee_value) * 100).toLocaleString("zh-CN", { maximumFractionDigits: 6 })}%` : `${money(row.fee_value)} 元/手` },
    { title: "平今/日内", render: (_, row) => !row.fee_close_today_mode ? "--" : row.fee_close_today_mode === "TURNOVER_RATE" ? `${(Number(row.fee_close_today_value) * 100).toLocaleString("zh-CN", { maximumFractionDigits: 6 })}%` : `${money(row.fee_close_today_value)} 元/手` },
    { title: "状态", dataIndex: "enabled", render: (value) => value ? <Tag color="success">启用</Tag> : <Tag>停用</Tag> },
    { title: "操作", render: (_, row) => <Button size="small" onClick={() => edit(row)}>编辑</Button> },
  ];
  return <div className="desk-page">{contextHolder}<PageTitle kicker="PRODUCT RISK DATA" title="品种参数" action={<Space><input ref={costFileInput} type="file" accept=".xlsx" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) importMutation.mutate(file); event.target.value = ""; }} /><Button icon={<Upload size={16} />} loading={importMutation.isPending} onClick={() => costFileInput.current?.click()}>保证金及手续费</Button><Button type="primary" onClick={() => edit()}>新增品种</Button></Space>} /><Alert showIcon type="info" message="选择品种后自动填入交易所、名称、合约乘数、最小变动价位、保证金率和手续费。同一品种的全部月份合约共用该标准。" /><section className="table-section"><Table rowKey="symbol" columns={columns} dataSource={query.data || []} loading={query.isLoading} pagination={false} /></section><Modal width={760} title={editing ? "编辑品种参数" : "新增品种参数"} open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={mutation.isPending}><Form form={form} layout="vertical" onFinish={(values) => mutation.mutate(values)}><div className="form-grid three"><Form.Item name="symbol" label="品种" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" loading={productsQuery.isLoading} disabled={Boolean(editing)} options={(productsQuery.data || []).map((item) => ({ value: item.symbol, label: `${item.symbol.toUpperCase()} · ${item.name}` }))} onChange={(value) => { const item = productsQuery.data?.find((product) => product.symbol === value); form.setFieldsValue({ exchange: item?.exchange, name: item?.name, multiplier: undefined, price_tick: undefined, margin_rate: undefined, fee_mode: undefined, fee_value: undefined, fee_close_today_mode: undefined, fee_close_today_value: undefined }); }} /></Form.Item><Form.Item name="exchange" label="交易所" rules={[{ required: true }]}><Input disabled /></Form.Item><Form.Item name="name" label="品种名称"><Input disabled /></Form.Item></div>{productDetailsQuery.isError ? <Alert showIcon type="warning" message="未能自动读取合约规格，可手工填写乘数和最小变动价位" description={productDetailsQuery.error.message} /> : null}<div className="form-grid three"><Form.Item name="multiplier" label="合约乘数" rules={[{ required: true }]}><InputNumber min={0.0001} className="full-input" /></Form.Item><Form.Item name="price_tick" label="最小变动价位" rules={[{ required: true }]}><InputNumber min={0.0001} className="full-input" /></Form.Item><Form.Item name="margin_rate" label="保证金率" rules={[{ required: true }]}><InputNumber min={0.0001} max={1} step={0.01} className="full-input" /></Form.Item></div><div className="form-grid"><Form.Item name="fee_mode" label="开/平仓手续费方式" rules={[{ required: true }]}><Select options={[{ value: "TURNOVER_RATE", label: "按成交额比例" }, { value: "PER_LOT", label: "按每手固定金额" }]} /></Form.Item><Form.Item name="fee_value" label={feeMode === "TURNOVER_RATE" ? "开/平仓手续费率" : "开/平仓每手手续费"} rules={[{ required: true }]}><InputNumber min={0} step={feeMode === "TURNOVER_RATE" ? 0.0001 : 0.01} addonAfter={feeMode === "TURNOVER_RATE" ? "%" : "元/手"} className="full-input" /></Form.Item></div><div className="form-grid"><Form.Item name="fee_close_today_mode" label="平今/日内手续费方式"><Select allowClear placeholder="无特殊平今/日内费率" options={[{ value: "TURNOVER_RATE", label: "按成交额比例" }, { value: "PER_LOT", label: "按每手固定金额" }]} /></Form.Item><Form.Item name="fee_close_today_value" label={closeTodayFeeMode === "TURNOVER_RATE" ? "平今/日内手续费率" : "平今/日内每手手续费"} rules={closeTodayFeeMode ? [{ required: true }] : []}><InputNumber disabled={!closeTodayFeeMode} min={0} step={closeTodayFeeMode === "TURNOVER_RATE" ? 0.0001 : 0.01} addonAfter={closeTodayFeeMode === "TURNOVER_RATE" ? "%" : "元/手"} className="full-input" /></Form.Item></div><Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item></Form></Modal></div>;
}

function PageTitle({ kicker, title, action }: { kicker: string; title: string; action?: React.ReactNode }) {
  return <header className="page-heading"><div><span className="page-kicker">{kicker}</span><Typography.Title level={2}>{title}</Typography.Title></div>{action}</header>;
}
