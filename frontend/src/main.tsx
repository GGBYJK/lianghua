import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { getDefaultConfig, getMarketSettings, getMarketSymbols, nextSimulationBar, resetSimulation, scanCsv, scanMarket, scanSample, startSimulation } from "./api";
import type { Candle, MarketSettings, MarketSymbol, Neckline, PivotPoint, ScanResponse, Signal, SimulationStartResponse } from "./types";
import "./styles.css";

echarts.use([
  AxisPointerComponent,
  BarChart,
  CandlestickChart,
  CanvasRenderer,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  LineChart,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
]);

const numericFields = [
  "pivot_left",
  "pivot_right",
  "min_head_above_shoulder_pct",
  "max_shoulder_diff_pct",
  "max_neck_diff_pct",
  "min_right_leg_to_left_leg_ratio",
  "max_right_leg_to_left_leg_ratio",
  "min_head_to_right_neck_to_left_neck_to_head_ratio",
  "max_head_to_right_neck_to_left_neck_to_head_ratio",
  "right_shoulder_volume_ratio",
  "break_volume_ratio",
  "neckline_break_pct",
  "max_bars_after_right_shoulder",
  "max_signal_age_bars",
  "min_score_to_alert",
];

const booleanFields = [
  "enable_right_shoulder_volume_weak",
  "enable_break_volume_confirm",
  "enable_ma_filter",
  "require_ma_bearish_alignment",
  "require_close_below_ma_long",
  "enable_macd_divergence",
];

const symbolTypes = [
  { value: "DCE", label: "大商所" },
  { value: "SHFE", label: "上期所" },
  { value: "CZCE", label: "郑商所" },
  { value: "CFFEX", label: "中金所" },
  { value: "FUTURES", label: "期货" },
  { value: "FOREX", label: "外汇" },
  { value: "ENERGY", label: "能源" },
  { value: "METAL", label: "金属" },
  { value: "CRYPTO", label: "加密货币" },
  { value: "STOCK_US", label: "美股" },
  { value: "STOCK_CN", label: "A股" },
  { value: "STOCK_HK", label: "港股" },
];

const MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache_v6";
const LEGACY_MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache";
const MARKET_SCAN_CACHE_VERSION = 6;

type AlertItem = {
  key: string;
  time: string;
  title: string;
  message: string;
  confirmed: boolean;
};

type CachedMarketScan = {
  version: number;
  savedAt: string;
  limit: number;
  result: ScanResponse;
};

function App() {
  const [symbol, setSymbol] = useState("c0");
  const [timeframe, setTimeframe] = useState("5m");
  const [file, setFile] = useState<File | null>(null);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [simulation, setSimulation] = useState<SimulationStartResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [intervalMs, setIntervalMs] = useState(900);
  const [barsPerTick, setBarsPerTick] = useState(1);
  const [cursor, setCursor] = useState(0);
  const [latestBar, setLatestBar] = useState<Candle | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [marketSettings, setMarketSettings] = useState<MarketSettings | null>(null);
  const [marketLimit, setMarketLimit] = useState(420);
  const [marketLastFetch, setMarketLastFetch] = useState<string | null>(null);
  const [symbolType, setSymbolType] = useState("FUTURES");
  const [symbolQuery, setSymbolQuery] = useState("");
  const [marketSymbols, setMarketSymbols] = useState<MarketSymbol[]>([]);
  const [symbolsLoading, setSymbolsLoading] = useState(false);
  const [cachedMarketScan, setCachedMarketScan] = useState<CachedMarketScan | null>(null);
  const seenSignalKeys = useRef<Set<string>>(new Set());

  useEffect(() => {
    getDefaultConfig(symbol, timeframe)
      .then(setConfig)
      .catch((err) => setError(err.message));
  }, [symbol, timeframe]);

  useEffect(() => {
    getMarketSettings()
      .then(setMarketSettings)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    const cached = readCachedMarketScan();
    if (cached) {
      setCachedMarketScan(cached);
    }
  }, []);

  useEffect(() => {
    if (!running || !simulation) {
      return;
    }
    const timer = window.setInterval(() => {
      void stepSimulation();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [running, simulation, intervalMs, barsPerTick]);

  async function runScan() {
    if (!file) {
      setError("请先选择 CSV 文件。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await scanCsv({ file, symbol, timeframe, overrides: config });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "扫描失败");
    } finally {
      setLoading(false);
    }
  }

  async function createSimulation() {
    if (!file) {
      setError("请先选择 CSV 文件。");
      return;
    }
    setLoading(true);
    setError(null);
    setRunning(false);
    seenSignalKeys.current.clear();
    setAlerts([]);
    setCursor(0);
    setLatestBar(null);
    try {
      const session = await startSimulation({ file, symbol, timeframe, overrides: config });
      setSimulation(session);
      setResult(null);
      await requestBrowserNotification();
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动模拟失败");
    } finally {
      setLoading(false);
    }
  }

  async function stepSimulation() {
    if (!simulation) {
      return;
    }
    try {
      const response = await nextSimulationBar(simulation.session_id, barsPerTick);
      setCursor(response.cursor);
      setLatestBar(response.latest_bar);
      setResult(response.scan);
      pushNewAlerts(response.scan.signals);
      if (response.done) {
        setRunning(false);
      }
    } catch (err) {
      setRunning(false);
      setError(err instanceof Error ? err.message : "模拟推进失败");
    }
  }

  async function resetCurrentSimulation() {
    if (!simulation) {
      return;
    }
    setRunning(false);
    await resetSimulation(simulation.session_id);
    seenSignalKeys.current.clear();
    setAlerts([]);
    setCursor(0);
    setLatestBar(null);
    setResult(null);
  }

  async function pollMarket() {
    setLoading(true);
    setError(null);
    try {
      const response = await scanMarket(symbol, timeframe, marketLimit);
      applyScanResponse(response);
      saveMarketScanCache(response, marketLimit);
      setMarketLastFetch(`接口 ${new Date().toLocaleTimeString()}`);
      pushNewAlerts(response.signals);
    } catch (err) {
      setError(err instanceof Error ? err.message : "实盘行情拉取失败");
    } finally {
      setLoading(false);
    }
  }

  function applyScanResponse(response: ScanResponse) {
    setResult(response);
    setCursor(response.rows);
    setLatestBar(response.chart.candles[response.chart.candles.length - 1] ?? null);
  }

  function saveMarketScanCache(response: ScanResponse, limit: number) {
    const cached: CachedMarketScan = {
      version: MARKET_SCAN_CACHE_VERSION,
      savedAt: new Date().toISOString(),
      limit,
      result: response,
    };
    setCachedMarketScan(cached);
    try {
      window.localStorage.setItem(MARKET_SCAN_CACHE_KEY, JSON.stringify(cached));
    } catch {
      setError("查询结果已显示，但浏览器缓存写入失败。");
    }
  }

  function loadCachedMarketScan() {
    const cached = cachedMarketScan ?? readCachedMarketScan();
    if (!cached) {
      setError("页面缓存里还没有实盘查询结果。");
      return;
    }
    setError(null);
    setCachedMarketScan(cached);
    setSymbol(cached.result.symbol);
    setTimeframe(cached.result.timeframe);
    setMarketLimit(cached.limit);
    applyScanResponse(cached.result);
    setMarketLastFetch(`缓存 ${formatTime(cached.savedAt)}`);
    pushNewAlerts(cached.result.signals);
  }

  async function loadMarketSymbols() {
    setSymbolsLoading(true);
    setError(null);
    try {
      const response = await getMarketSymbols(symbolType);
      setMarketSymbols(response.symbols);
    } catch (err) {
      setError(err instanceof Error ? err.message : "产品列表查询失败");
    } finally {
      setSymbolsLoading(false);
    }
  }

  async function loadSampleData() {
    setLoading(true);
    setError(null);
    try {
      const response = await scanSample("TEST", "5m");
      setSymbol(response.symbol);
      setTimeframe(response.timeframe);
      setResult(response);
      setCursor(response.rows);
      setLatestBar(response.chart.candles[response.chart.candles.length - 1] ?? null);
      setMarketLastFetch("测试数据");
      pushNewAlerts(response.signals);
    } catch (err) {
      setError(err instanceof Error ? err.message : "测试数据加载失败");
    } finally {
      setLoading(false);
    }
  }

  function pushNewAlerts(signals: Signal[]) {
    const newAlerts: AlertItem[] = [];
    for (const signal of signals) {
      const key = signalKey(signal);
      if (seenSignalKeys.current.has(key)) {
        continue;
      }
      seenSignalKeys.current.add(key);
      const title = `${patternLabel(signal.pattern)}${signal.confirmed ? "确认信号" : "疑似信号"}`;
      const message = translateResultText(signal.message);
      newAlerts.push({
        key,
        time: signal.break_time ?? signal.right_shoulder.time,
        title,
        message,
        confirmed: signal.confirmed,
      });
      sendBrowserNotification(title, message);
    }
    if (newAlerts.length > 0) {
      setAlerts((prev) => [...newAlerts, ...prev].slice(0, 20));
    }
  }

  const confirmed = result?.signals.filter((signal) => signal.confirmed) ?? [];
  const suspected = result?.signals.filter((signal) => !signal.confirmed) ?? [];
  const visibleMarketSymbols = marketSymbols
    .filter((item) => symbolMatches(item, symbolQuery))
    .slice(0, 80);
  const totalRows = simulation?.total_rows ?? result?.rows ?? 0;
  const progress = totalRows > 0 ? Math.round((cursor / totalRows) * 100) : 0;

  return (
    <main className="app-shell">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">CSV 模拟实盘</p>
          <h1>头肩顶实时监控工作台</h1>
          <p className="hero-copy">
            上传历史K线后按时间逐根回放，模拟实盘不断扫描头肩顶结构；出现疑似或确认信号时，会写入信号流并触发浏览器通知。
          </p>
        </div>
        <div className="hero-stats">
          <Metric label="已接收K线" value={simulation ? `${cursor}/${simulation.total_rows}` : result?.rows ?? "-"} />
          <Metric label="信号总数" value={result?.signals.length ?? "-"} />
          <Metric label="确认信号" value={confirmed.length || "-"} tone="hot" />
        </div>
      </section>

      <section className="workspace">
        <aside className="control-panel">
          <h2>数据输入</h2>
          <label>
            合约代码
            <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
          </label>
          <label>
            周期
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="1m">1m</option>
              <option value="5m">5m</option>
              <option value="15m">15m</option>
              <option value="30m">30m</option>
              <option value="1h">1h</option>
              <option value="1d">1d</option>
            </select>
          </label>
          <label className="file-drop">
            <span>{file ? file.name : "选择 CSV 文件"}</span>
            <input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </label>
          <button disabled={loading} onClick={runScan}>{loading ? "处理中..." : "一次性扫描"}</button>
          <button className="secondary-button" disabled={loading} onClick={() => void loadSampleData()}>使用测试数据</button>
          {error && <div className="error-box">{error}</div>}

          <h2>模拟实盘</h2>
          <div className="sim-actions">
            <button disabled={loading || !file} onClick={createSimulation}>上传并创建模拟</button>
            <button disabled={!simulation || loading} onClick={() => setRunning((value) => !value)}>
              {running ? "暂停回放" : "开始回放"}
            </button>
            <button disabled={!simulation || loading} onClick={() => void stepSimulation()}>推进一批K线</button>
            <button disabled={!simulation || loading} onClick={() => void resetCurrentSimulation()}>重置回放</button>
          </div>

          <h2>实盘接口监控</h2>
          <div className="progress-box">
            <div className="progress-meta">
              <span>接口地址</span>
              <strong>{marketSettings?.api_key_set === "是" ? "已配置" : "未配置"}</strong>
            </div>
            <p>{marketSettings?.provider ?? "行情源"}：{marketSettings?.base_url ?? "未知"}，参数 {marketSettings?.market_module ?? "period"}。未配置时，请先设置对应密钥。</p>
            {marketLastFetch && <p>最近拉取：{marketLastFetch}</p>}
          </div>
          <div className="symbol-browser">
            <div className="symbol-browser-head">
              <h3>产品列表</h3>
              <span>{marketSymbols.length || "-"} 项</span>
            </div>
            <label>
              产品类型
              <select value={symbolType} onChange={(event) => setSymbolType(event.target.value)}>
                {symbolTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label>
              搜索产品
              <input
                value={symbolQuery}
                onChange={(event) => setSymbolQuery(event.target.value)}
                placeholder="代码 / 中文名 / 英文名"
              />
            </label>
            <button className="secondary-button compact-button" disabled={symbolsLoading} onClick={() => void loadMarketSymbols()}>
              {symbolsLoading ? "加载中..." : "查询产品列表"}
            </button>
            {marketSymbols.length > 0 && (
              <div className="symbol-list">
                {visibleMarketSymbols.map((item) => (
                  <button
                    type="button"
                    className={`symbol-option ${item.symbol === symbol ? "selected" : ""}`}
                    key={item.symbol}
                    onClick={() => setSymbol(item.symbol)}
                  >
                    <strong>{item.symbol}</strong>
                    <span>{displaySymbolName(item)}</span>
                  </button>
                ))}
                {visibleMarketSymbols.length === 0 && <p className="symbol-empty">没有匹配的产品</p>}
              </div>
            )}
          </div>
          <label>
            每次拉取K线数量
            <input type="number" min={30} max={500} value={marketLimit} onChange={(event) => setMarketLimit(Number(event.target.value))} />
          </label>
          <button disabled={loading} onClick={() => void pollMarket()}>{loading ? "拉取中..." : "点击获取一次实盘K线并扫描1"}</button>
          <button className="secondary-button" disabled={!cachedMarketScan} onClick={loadCachedMarketScan}>使用页面缓存结果</button>
          {cachedMarketScan && (
            <p className="cache-note">
              已缓存 {cachedMarketScan.result.symbol} / {cachedMarketScan.result.timeframe} / {cachedMarketScan.result.rows} 根，{formatTime(cachedMarketScan.savedAt)}
            </p>
          )}
          <label>
            回放间隔（毫秒）
            <input type="number" min={200} step={100} value={intervalMs} onChange={(event) => setIntervalMs(Number(event.target.value))} />
          </label>
          <label>
            每次推送K线数
            <input type="number" min={1} max={50} value={barsPerTick} onChange={(event) => setBarsPerTick(Number(event.target.value))} />
          </label>
          <div className="progress-box">
            <div className="progress-meta">
              <span>{simulation ? "模拟会话已创建" : "未创建模拟会话"}</span>
              <strong>{progress}%</strong>
            </div>
            <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
            {latestBar && <p>最新K线：{formatTime(latestBar.time)}，收盘 {formatPrice(latestBar.close)}，成交量 {latestBar.volume}</p>}
          </div>

          <h2>关键参数</h2>
          <div className="config-grid">
            {numericFields.map((field) => (
              <label key={field}>
                {fieldLabel(field)}
                <input
                  type="number"
                  step="0.001"
                  value={String(config[field] ?? "")}
                  onChange={(event) => setConfig((prev) => ({ ...prev, [field]: Number(event.target.value) }))}
                />
              </label>
            ))}
          </div>
          <div className="switch-list">
            {booleanFields.map((field) => (
              <label key={field} className="switch-row">
                <input
                  type="checkbox"
                  checked={Boolean(config[field])}
                  onChange={(event) => setConfig((prev) => ({ ...prev, [field]: event.target.checked }))}
                />
                {fieldLabel(field)}
              </label>
            ))}
          </div>
        </aside>

        <section className="result-panel">
          <div className="panel-head">
            <div>
              <h2>实时K线结构</h2>
              <p>{result ? `${result.start_time} - ${result.end_time}` : "上传 CSV 后可一次性扫描，也可以创建模拟实盘逐根推送。"} </p>
            </div>
            <span className="badge">{result?.symbol ?? symbol} / {result?.timeframe ?? timeframe}</span>
          </div>
          <KlineChartEcharts
            candles={result?.chart.candles ?? []}
            pivots={result?.chart.pivots ?? []}
            necklines={result?.chart.necklines ?? []}
            signals={result?.signals ?? []}
          />

          <div className="alert-feed">
            <div className="panel-head compact">
              <h2>信号发送记录</h2>
              <span className="badge">{alerts.length} 条</span>
            </div>
            {alerts.length === 0 ? (
              <p className="empty">暂无发送记录。模拟回放中出现新信号后会显示在这里。</p>
            ) : alerts.map((alert) => (
              <article className={`alert-item ${alert.confirmed ? "confirmed" : ""}`} key={alert.key}>
                <strong>{alert.title}</strong>
                <span>{formatTime(alert.time)}</span>
                <p>{alert.message}</p>
              </article>
            ))}
          </div>

          <div className="signal-columns">
            <SignalGroup title="确认信号" signals={confirmed} empty="暂无确认头肩顶" />
            <SignalGroup title="疑似信号" signals={suspected} empty="暂无疑似结构" />
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "hot" }) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SignalGroup({ title, signals, empty }: { title: string; signals: Signal[]; empty: string }) {
  return (
    <div className="signal-group">
      <h3>{title}</h3>
      {signals.length === 0 ? <p className="empty">{empty}</p> : signals.map((signal, index) => (
        <article className={`signal-card ${signal.confirmed ? "confirmed" : ""}`} key={`${signal.head.time}-${index}`}>
          <div className="signal-top">
            <strong>{signal.score}</strong>
            <span>{patternLabel(signal.pattern)} · {signal.confirmed ? "已确认" : "疑似"}</span>
          </div>
          <p>{translateResultText(signal.message)}</p>
          <dl>
            <div><dt>左肩</dt><dd>{formatPrice(signal.left_shoulder.price)}</dd></div>
            <div><dt>头部</dt><dd>{formatPrice(signal.head.price)}</dd></div>
            <div><dt>右肩</dt><dd>{formatPrice(signal.right_shoulder.price)}</dd></div>
            <div><dt>颈线</dt><dd>{formatPrice(signal.neckline_price)}</dd></div>
          </dl>
          <ul>
            {signal.reasons.slice(0, 8).map((reason) => <li key={reason}>{translateResultText(reason)}</li>)}
          </ul>
        </article>
      ))}
    </div>
  );
}

function KlineChartEcharts({ candles, signals }: {
  candles: Candle[];
  pivots: PivotPoint[];
  necklines: Neckline[];
  signals: Signal[];
}) {
  const chartRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!chartRef.current || candles.length === 0) {
      return;
    }

    const chart = echarts.init(chartRef.current, "dark", { renderer: "canvas" });
    const categories = candles.map((candle) => formatShortTime(candle.time));
    const ohlc = candles.map((candle) => [candle.open, candle.close, candle.low, candle.high]);
    const volumes = candles.map((candle) => ({
      value: candle.volume,
      itemStyle: { color: candle.close >= candle.open ? "rgba(239,68,68,0.48)" : "rgba(34,197,94,0.48)" },
    }));
    const maKeys = Object.keys(candles.find((candle) => candle.ma && Object.keys(candle.ma).length > 0)?.ma ?? {})
      .sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
    const maColors: Record<string, string> = {
      ma5: "#f2c66d",
      ma10: "#78c6ff",
      ma20: "#d48cff",
      ma30: "#68d391",
      ma60: "#ef7b6d",
      ma250: "#cbd5e1",
    };
    const visibleSignals = signals;
    const patternLabels = ["左肩", "左颈", "头部", "右颈", "右肩"];
    const markPoints = visibleSignals.flatMap((signal) => [
      signal.left_shoulder,
      signal.left_neck,
      signal.head,
      signal.right_neck,
      signal.right_shoulder,
    ].map((point, index) => ({
      name: `${signal.confirmed ? "确认" : "疑似"}${patternLabels[index]}`,
      coord: [point.index, point.price],
      value: patternLabels[index],
      itemStyle: {
        color: signal.confirmed ? (index === 2 ? "#f59e0b" : "#eab308") : (index === 2 ? "#8dd3c7" : "#94a3b8"),
        borderColor: "#07100d",
        borderWidth: 2,
      },
      label: {
        formatter: signal.confirmed ? patternLabels[index] : `疑${patternLabels[index]}`,
        color: "#f8fafc",
        fontSize: 11,
        fontWeight: 700,
      },
    })));
    const markLines = visibleSignals.map((signal) => {
      const breakIndex = signal.break_time ? candles.findIndex((candle) => candle.time === signal.break_time) : -1;
      const toIndex = breakIndex >= 0 ? breakIndex : signal.right_shoulder.index;
      return [
        {
          coord: [signal.left_neck.index, signal.left_neck.price],
          lineStyle: {
            color: signal.confirmed ? "#facc15" : "#8dd3c7",
            width: signal.confirmed ? 2 : 1.5,
            type: signal.confirmed ? "dashed" : "dotted",
          },
        },
        { coord: [toIndex, calculateChartNeckline(signal.left_neck, signal.right_neck, toIndex)] },
      ];
    });
    const start = candles.length > 160 ? Math.max(0, 100 - (160 / candles.length) * 100) : 0;

    chart.setOption({
      backgroundColor: "#07100d",
      animation: false,
      color: maKeys.map((key) => maColors[key] ?? "#94a3b8"),
      legend: {
        top: 8,
        left: 14,
        icon: "roundRect",
        itemWidth: 18,
        itemHeight: 3,
        data: maKeys.map((key) => key.toUpperCase()),
        textStyle: { color: "#b6c2bc", fontSize: 11, fontWeight: 700 },
      },
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
        label: { backgroundColor: "#1f2937", color: "#e5e7eb" },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.32)",
        backgroundColor: "rgba(7,16,13,0.94)",
        textStyle: { color: "#e7efe8", fontSize: 12 },
        extraCssText: "box-shadow: 0 12px 32px rgba(0,0,0,.35); border-radius: 8px;",
        formatter: (params: unknown) => formatChartTooltip(params, candles),
      },
      grid: [
        { left: 14, right: 58, top: 34, height: 292 },
        { left: 14, right: 58, top: 352, height: 78 },
      ],
      xAxis: [
        {
          type: "category",
          data: categories,
          boundaryGap: true,
          axisLine: { lineStyle: { color: "rgba(148,163,184,0.28)" } },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
        {
          type: "category",
          gridIndex: 1,
          data: categories,
          boundaryGap: true,
          axisLine: { lineStyle: { color: "rgba(148,163,184,0.28)" } },
          axisTick: { show: false },
          axisLabel: { color: "#91a39a", fontSize: 10, hideOverlap: true },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: "right",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: "#a6b6ae", fontSize: 11 },
          splitLine: { lineStyle: { color: "rgba(148,163,184,0.10)" } },
        },
        {
          scale: true,
          gridIndex: 1,
          position: "right",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: "#91a39a", fontSize: 10, formatter: (value: number) => formatCompactVolume(value) },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start, end: 100, zoomOnMouseWheel: true, moveOnMouseWheel: true, moveOnMouseMove: true },
        { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 18, borderColor: "rgba(148,163,184,0.18)", fillerColor: "rgba(121,183,164,0.16)", handleStyle: { color: "#79b7a4" }, textStyle: { color: "#91a39a" } },
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          data: ohlc,
          itemStyle: {
            color: "rgba(239,68,68,0.78)",
            color0: "rgba(34,197,94,0.78)",
            borderColor: "#ef4444",
            borderColor0: "#22c55e",
          },
          markPoint: { symbol: "circle", symbolSize: 12, data: markPoints },
          markLine: {
            symbol: "none",
            lineStyle: { color: "#facc15", width: 2, type: "dashed" },
            label: { color: "#f8fafc", formatter: "颈线" },
            data: markLines,
          },
        },
        ...maKeys.map((key) => ({
          name: key.toUpperCase(),
          type: "line",
          data: candles.map((candle) => candle.ma?.[key] ?? null),
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.6, color: maColors[key] ?? "#94a3b8" },
          connectNulls: false,
          emphasis: { disabled: true },
        })),
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          barWidth: "60%",
          large: true,
        },
      ],
    });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [candles, signals]);

  if (candles.length === 0) {
    return <div className="chart-empty">等待 K 线数据</div>;
  }

  return <div className="chart-wrap"><div ref={chartRef} className="echart-kline" /></div>;
}

function KlineChart({ candles, pivots, necklines, signals }: {
  candles: Candle[];
  pivots: PivotPoint[];
  necklines: Neckline[];
  signals: Signal[];
}) {
  if (candles.length === 0) {
    return <div className="chart-empty">等待 CSV 数据</div>;
  }

  const width = 940;
  const height = 460;
  const pad = 28;
  const rightAxis = 62;
  const priceHeight = 326;
  const volumeTop = 354;
  const volumeHeight = 76;
  const prices = candles.flatMap((candle) => [candle.high, candle.low]);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const chartWidth = width - pad - rightAxis;
  const x = (index: number) => pad + (index / Math.max(1, candles.length - 1)) * chartWidth;
  const y = (price: number) => pad + ((max - price) / range) * (priceHeight - pad * 2);
  const slotWidth = chartWidth / Math.max(1, candles.length);
  const bodyWidth = Math.max(1, Math.min(7, slotWidth * 0.58));
  const volumeWidth = Math.max(1, Math.min(6, slotWidth * 0.7));
  const maxVolume = Math.max(...candles.map((candle) => candle.volume), 1);
  const volumeY = (volume: number) => volumeTop + volumeHeight - (volume / maxVolume) * volumeHeight;
  const priceTicks = Array.from({ length: 5 }, (_, index) => max - (range / 4) * index);
  const timeTickStep = Math.max(1, Math.ceil(candles.length / 5));
  const timeTicks = candles.filter((candle, index) => index % timeTickStep === 0 || index === candles.length - 1);
  const visibleNecklines = necklines;
  const maKeys = Object.keys(candles.find((candle) => candle.ma && Object.keys(candle.ma).length > 0)?.ma ?? {}).sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
  const maPath = (key: string) => {
    const points = candles
      .filter((candle) => candle.ma?.[key] !== null && candle.ma?.[key] !== undefined)
      .map((candle) => ({ x: x(candle.index), y: y(Number(candle.ma?.[key])) }));
    if (points.length === 0) {
      return "";
    }
    if (points.length === 1) {
      return `M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
    }
    const commands = [`M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`];
    for (let index = 1; index < points.length; index += 1) {
      const previous = points[index - 1];
      const current = points[index];
      const controlX = (previous.x + current.x) / 2;
      commands.push(
        `Q ${previous.x.toFixed(2)} ${previous.y.toFixed(2)} ${controlX.toFixed(2)} ${((previous.y + current.y) / 2).toFixed(2)}`,
      );
      commands.push(`T ${current.x.toFixed(2)} ${current.y.toFixed(2)}`);
    }
    return commands.join(" ");
  };

  const patternPoints = signals.flatMap((signal) => [
    signal.left_shoulder,
    signal.left_neck,
    signal.head,
    signal.right_neck,
    signal.right_shoulder,
  ].map((point) => ({ point, confirmed: signal.confirmed })));

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="带头肩顶标记的K线图">
        <defs>
          <linearGradient id="gridFade" x1="0" x2="1">
            <stop offset="0%" stopColor="rgba(148,163,184,0.06)" />
            <stop offset="100%" stopColor="rgba(148,163,184,0.16)" />
          </linearGradient>
        </defs>
        <rect x={0} y={0} width={width} height={height} className="chart-bg" />
        {priceTicks.map((price) => {
          const gy = y(price);
          return (
            <g key={price}>
              <line x1={pad} x2={pad + chartWidth} y1={gy} y2={gy} className="grid-line" />
              <text x={width - 20} y={gy + 4} className="price-tick">{formatPrice(price)}</text>
            </g>
          );
        })}
        <line x1={pad} x2={pad + chartWidth} y1={priceHeight} y2={priceHeight} className="panel-divider" />
        <text x={pad + 4} y={volumeTop - 9} className="volume-label">成交量</text>
        <text x={width - 20} y={volumeTop + 8} className="volume-tick">{formatCompactVolume(maxVolume)}</text>
        {timeTicks.map((candle) => (
          <g key={`time-${candle.index}`}>
            <line x1={x(candle.index)} x2={x(candle.index)} y1={height - pad + 4} y2={height - pad + 9} className="axis-tick" />
            <text x={x(candle.index)} y={height - 8} className="time-tick">{formatShortTime(candle.time)}</text>
          </g>
        ))}
        {candles.map((candle) => {
          const up = candle.close >= candle.open;
          const cx = x(candle.index);
          const openY = y(candle.open);
          const closeY = y(candle.close);
          return (
            <g key={candle.index} className={up ? "candle up" : "candle down"}>
              <line x1={cx} x2={cx} y1={y(candle.high)} y2={y(candle.low)} />
              <rect x={cx - bodyWidth / 2} y={Math.min(openY, closeY)} width={bodyWidth} height={Math.max(2, Math.abs(closeY - openY))} />
            </g>
          );
        })}
        {maKeys.map((key) => <path key={key} d={maPath(key)} className={`ma-line ${key}`} />)}
        <g>
          {candles.map((candle) => {
            const up = candle.close >= candle.open;
            const cx = x(candle.index);
            const vy = volumeY(candle.volume);
            return (
              <rect
                key={`volume-${candle.index}`}
                x={cx - volumeWidth / 2}
                y={vy}
                width={volumeWidth}
                height={Math.max(1, volumeTop + volumeHeight - vy)}
                className={up ? "volume-bar up" : "volume-bar down"}
              />
            );
          })}
        </g>
        <g className="ma-legend">
          {maKeys.map((key, index) => (
            <text key={key} x={pad + 8 + index * 58} y={18} className={`ma-label ${key}`}>{key.toUpperCase()}</text>
          ))}
        </g>
        {visibleNecklines.map((neckline, index) => (
          <line
            key={index}
            x1={x(neckline.from_index)}
            y1={y(neckline.from_price)}
            x2={x(neckline.to_index)}
            y2={y(neckline.to_price)}
            className={`neckline ${neckline.confirmed ? "confirmed" : "suspected"}`}
          />
        ))}
        {patternPoints.map(({ point, confirmed }, index) => (
          <g key={`${point.time}-${index}`}>
            <circle cx={x(point.index)} cy={y(point.price)} r={6} className={`pattern-point ${confirmed ? "confirmed" : "suspected"}`} />
            <text x={x(point.index)} y={y(point.price) - 10}>{confirmed ? labelForPoint(index % 5) : `疑${labelForPoint(index % 5)}`}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function signalKey(signal: Signal) {
  return [
    signal.pattern,
    signal.confirmed ? "确认" : "疑似",
    signal.left_shoulder.index,
    signal.head.index,
    signal.right_shoulder.index,
    signal.break_time ?? "未跌破",
  ].join("-");
}

function displaySymbolName(item: MarketSymbol) {
  return item.name_cn || item.name_en || item.name_hk || "未命名产品";
}

function symbolMatches(item: MarketSymbol, query: string) {
  const keyword = query.trim().toLowerCase();
  if (!keyword) {
    return true;
  }
  return [item.symbol, item.name_cn, item.name_hk, item.name_en]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(keyword));
}

function patternLabel(pattern: Signal["pattern"]) {
  return pattern === "inverse_head_shoulders" ? "反向头肩顶" : "头肩顶";
}

function calculateChartNeckline(leftNeck: PivotPoint, rightNeck: PivotPoint, currentIndex: number) {
  if (leftNeck.index === rightNeck.index) {
    return rightNeck.price;
  }
  const slope = (rightNeck.price - leftNeck.price) / (rightNeck.index - leftNeck.index);
  return leftNeck.price + slope * (currentIndex - leftNeck.index);
}

function formatChartTooltip(params: unknown, candles: Candle[]) {
  const items = Array.isArray(params) ? params as Array<{ dataIndex?: number; seriesName?: string; value?: unknown; marker?: string }> : [];
  const dataIndex = items.find((item) => typeof item.dataIndex === "number")?.dataIndex;
  if (dataIndex === undefined) {
    return "";
  }
  const candle = candles[dataIndex];
  if (!candle) {
    return "";
  }
  const maRows = Object.entries(candle.ma ?? {})
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `<div><span style="color:#91a39a">${key.toUpperCase()}</span> ${formatPrice(Number(value))}</div>`)
    .join("");
  return [
    `<div style="font-weight:700;margin-bottom:6px">${formatTime(candle.time)}</div>`,
    `<div>开 ${formatPrice(candle.open)} &nbsp; 高 ${formatPrice(candle.high)}</div>`,
    `<div>低 ${formatPrice(candle.low)} &nbsp; 收 ${formatPrice(candle.close)}</div>`,
    `<div>量 ${formatCompactVolume(candle.volume)}</div>`,
    maRows ? `<div style="height:1px;background:rgba(148,163,184,.18);margin:6px 0"></div>${maRows}` : "",
  ].join("");
}

function readCachedMarketScan(): CachedMarketScan | null {
  try {
    const raw = window.localStorage.getItem(MARKET_SCAN_CACHE_KEY);
    if (!raw) {
      window.localStorage.removeItem(LEGACY_MARKET_SCAN_CACHE_KEY);
      window.localStorage.removeItem("lh_demo_market_scan_cache_v2");
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<CachedMarketScan>;
    if (
      parsed.version !== MARKET_SCAN_CACHE_VERSION ||
      !parsed.savedAt ||
      !parsed.result ||
      typeof parsed.limit !== "number"
    ) {
      return null;
    }
    return parsed as CachedMarketScan;
  } catch {
    return null;
  }
}

async function requestBrowserNotification() {
  if (!("Notification" in window)) {
    return;
  }
  if (Notification.permission === "default") {
    await Notification.requestPermission();
  }
}

function sendBrowserNotification(title: string, message: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }
  new Notification(title, { body: message });
}

function labelForPoint(index: number) {
  return ["左肩", "左颈", "头", "右颈", "右肩"][index];
}

function formatPrice(value: number) {
  return value.toFixed(2);
}

function formatTime(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

function formatShortTime(value: string) {
  const normalized = value.replace("T", " ");
  const datePart = normalized.slice(5, 10);
  const timePart = normalized.slice(11, 16);
  return `${datePart} ${timePart}`;
}

function formatCompactVolume(value: number) {
  if (value >= 10000) {
    return `${(value / 10000).toFixed(1)}万`;
  }
  return value.toFixed(0);
}

function fieldLabel(field: string) {
  const labels: Record<string, string> = {
    pivot_left: "左侧拐点窗口",
    pivot_right: "右侧拐点窗口",
    min_head_above_shoulder_pct: "头部高于肩部比例",
    max_shoulder_diff_pct: "左右肩最大差异",
    max_neck_diff_pct: "颈线低点最大差异",
    min_right_leg_to_left_leg_ratio: "右颈到右肩/左肩到左颈下限",
    max_right_leg_to_left_leg_ratio: "右颈到右肩/左肩到左颈上限",
    min_head_to_right_neck_to_left_neck_to_head_ratio: "头部到右颈/左颈到头部下限",
    max_head_to_right_neck_to_left_neck_to_head_ratio: "头部到右颈/左颈到头部上限",
    right_shoulder_volume_ratio: "右肩/头部量能上限",
    break_volume_ratio: "跌破放量倍数",
    neckline_break_pct: "颈线跌破幅度",
    max_bars_after_right_shoulder: "右肩后观察K线数",
    max_signal_age_bars: "仅返回最近N根内信号",
    min_score_to_alert: "最低提醒评分",
    enable_right_shoulder_volume_weak: "启用右肩缩量",
    enable_break_volume_confirm: "启用跌破放量确认",
    enable_ma_filter: "启用均线过滤",
    require_ma_bearish_alignment: "要求均线空头排列",
    require_close_below_ma_long: "要求收盘价低于长均线",
    enable_macd_divergence: "启用 MACD 顶背离",
  };
  return labels[field] ?? field;
}

function translateResultText(text: string) {
  return text
    .replace("head-and-shoulders top confirmed", "头肩顶确认")
    .replace("suspected head-and-shoulders top", "疑似头肩顶")
    .replace("waiting for neckline break", "等待跌破颈线确认")
    .replace("score", "评分")
    .replace("Score", "评分")
    .replace("Break price", "跌破价格")
    .replace("break price", "跌破价格")
    .replace("neckline", "颈线")
    .replace("Neckline", "颈线")
    .replace("head is clearly above both shoulders", "头部明显高于左右肩")
    .replace(/shoulders are close, diff ([\d.]+)%/g, "左右肩高度接近，差异 $1%")
    .replace(/neck lows are close, diff ([\d.]+)%/g, "两个颈线低点接近，差异 $1%")
    .replace("right shoulder is not excessively weak", "右肩没有过度走弱")
    .replace("right shoulder is below head", "右肩低于头部")
    .replace(/right shoulder volume weakened, ratio ([\d.]+)/g, "右肩成交量减弱，右肩/头部量能比 $1")
    .replace("MACD top divergence: price new high but MACD histogram lower", "出现 MACD 顶背离：头部价格创新高，但 MACD柱 降低")
    .replace("MACD top divergence: price new high but DIF lower", "出现 MACD 顶背离：头部价格创新高，但 DIF 降低")
    .replace(/neckline break confirmed, break ([\d.]+), neckline ([\d.]+), volume ([\d.]+)x/g, "跌破颈线确认，跌破价 $1，颈线价 $2，成交量放大 $3 倍")
    .replace(/neckline break confirmed, break ([\d.]+), neckline ([\d.]+)/g, "跌破颈线确认，跌破价 $1，颈线价 $2")
    .replace(/close is below MA(\d+)/g, "收盘价在 MA$1 下方")
    .replace("MA filter passed", "均线过滤通过")
    .replace("Confirmed", "已确认")
    .replace("Suspected", "疑似");
}

createRoot(document.getElementById("root")!).render(<App />);
