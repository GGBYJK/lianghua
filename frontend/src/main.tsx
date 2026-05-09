import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { getDefaultConfig, getMarketSettings, scanMarket } from "./api";
import type { Candle, MarketSettings, Neckline, PivotPoint, ScanResponse, Signal } from "./types";
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

const futuresSymbolOptions = [
  { symbol: "SR2609", name: "白糖2609" },
  { symbol: "SA2609", name: "纯碱2609" },
  { symbol: "RM2609", name: "菜粕2609" },
  { symbol: "FG2609", name: "玻璃2609" },
  { symbol: "CF2609", name: "棉花2609" },
  { symbol: "v2609", name: "PVC2609" },
  { symbol: "m2609", name: "豆粕2609" },
  { symbol: "jm2609", name: "焦煤2609" },
  { symbol: "cs2607", name: "淀粉2607" },
  { symbol: "c2607", name: "玉米2607" },
  { symbol: "a2607", name: "豆一2607" },
  { symbol: "sp2609", name: "纸浆2609" },
  { symbol: "hc2610", name: "热卷2610" },
  { symbol: "jd2606", name: "鸡蛋2606" },
  { symbol: "SF2607", name: "硅铁2607" },
  { symbol: "UR2609", name: "尿素2609" },
];

const MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache_v6";
const LEGACY_MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache";
const MARKET_SCAN_CACHE_VERSION = 6;

type CachedMarketScan = {
  version: number;
  savedAt: string;
  limit: number;
  result: ScanResponse;
};

function App() {
  const [symbol, setSymbol] = useState("c0");
  const [timeframe, setTimeframe] = useState("5m");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [latestBar, setLatestBar] = useState<Candle | null>(null);
  const [selectedSignalKeys, setSelectedSignalKeys] = useState<Set<string>>(new Set());
  const [focusedSignalKey, setFocusedSignalKey] = useState<string | null>(null);
  const [marketSettings, setMarketSettings] = useState<MarketSettings | null>(null);
  const [marketLimit, setMarketLimit] = useState(420);
  const [marketLastFetch, setMarketLastFetch] = useState<string | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [symbolPickerOpen, setSymbolPickerOpen] = useState(false);
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
    document.body.classList.toggle("modal-open", configOpen);
    return () => document.body.classList.remove("modal-open");
  }, [configOpen]);

  async function pollMarket() {
    setLoading(true);
    setError(null);
    try {
      await requestBrowserNotification();
      const response = await scanMarket(symbol, timeframe, marketLimit, config);
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
    setSelectedSignalKeys(new Set());
    setFocusedSignalKey(null);
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
    try {
      window.localStorage.setItem(MARKET_SCAN_CACHE_KEY, JSON.stringify(cached));
    } catch {
      setError("查询结果已显示，但浏览器缓存写入失败。");
    }
  }

  function pushNewAlerts(signals: Signal[]) {
    for (const signal of signals) {
      const key = signalKey(signal);
      if (seenSignalKeys.current.has(key)) {
        continue;
      }
      seenSignalKeys.current.add(key);
      const title = `${patternLabel(signal.pattern)}${signal.confirmed ? "确认信号" : "疑似信号"}`;
      const message = translateResultText(signal.message);
      sendBrowserNotification(title, message);
    }
  }

  const confirmed = result?.signals.filter((signal) => signal.confirmed) ?? [];
  const suspected = result?.signals.filter((signal) => !signal.confirmed) ?? [];
  const selectedSignals = result?.signals.filter((signal) => selectedSignalKeys.has(signalKey(signal))) ?? [];
  const focusedSignal = selectedSignals.find((signal) => signalKey(signal) === focusedSignalKey) ?? null;
  const allSignals = result?.signals ?? [];
  const allSignalKeys = allSignals.map(signalKey);
  const selectedCount = selectedSignals.length;
  const visibleFuturesSymbolOptions = futuresSymbolOptions.filter((item) => {
    const keyword = symbol.trim().toLowerCase();
    if (!keyword) {
      return true;
    }
    return item.symbol.toLowerCase().includes(keyword) || item.name.toLowerCase().includes(keyword);
  });
  const totalRows = result?.rows ?? 0;
  const progress = totalRows > 0 ? Math.round((cursor / totalRows) * 100) : 0;

  function toggleSignalSelection(signal: Signal) {
    const key = signalKey(signal);
    setSelectedSignalKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
        setFocusedSignalKey((current) => (current === key ? null : current));
      } else {
        next.add(key);
        setFocusedSignalKey(key);
      }
      return next;
    });
  }

  function showAllSignals() {
    setSelectedSignalKeys(new Set(allSignalKeys));
    setFocusedSignalKey(allSignalKeys[0] ?? null);
  }

  function clearSelectedSignals() {
    setSelectedSignalKeys(new Set());
    setFocusedSignalKey(null);
  }

  return (
    <main className="app-shell">

      <section className="workspace">
        <aside className="control-panel">
          <div className="control-head">
            <div>
              <p className="eyebrow">Live Scan</p>
              <h2>实盘操作</h2>
            </div>
            <button className="icon-button" type="button" onClick={() => setConfigOpen(true)} aria-label="打开配置">
              配置
            </button>
          </div>
          <div className="progress-box">
            <div className="progress-meta">
              <span>接口地址</span>
              <strong>{marketSettings?.api_key_set === "是" ? "已配置" : "未配置"}</strong>
            </div>
            <p>{marketSettings?.provider ?? "行情源"}：{marketSettings?.base_url ?? "未知"}，参数 {marketSettings?.market_module ?? "period"}。未配置时，请先设置对应密钥。</p>
            {marketLastFetch && <p>最近拉取：{marketLastFetch}</p>}
          </div>
          <div className="market-form">
            <div className="symbol-combobox">
              <label htmlFor="symbol-input">合约代码</label>
              <div className="symbol-input-row">
                <input
                  id="symbol-input"
                  value={symbol}
                  onChange={(event) => {
                    setSymbol(event.target.value);
                    setSymbolPickerOpen(true);
                  }}
                  onFocus={() => setSymbolPickerOpen(true)}
                  placeholder="选择合约或手动输入，例如 hc2610"
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="symbol-picker-toggle"
                  onClick={() => setSymbolPickerOpen((value) => !value)}
                  aria-label="展开合约列表"
                >
                  ▾
                </button>
              </div>
              {symbolPickerOpen && (
                <div className="symbol-picker" role="listbox">
                  {(visibleFuturesSymbolOptions.length > 0 ? visibleFuturesSymbolOptions : futuresSymbolOptions).map((item) => (
                    <button
                      type="button"
                      className="symbol-picker-option"
                      key={item.symbol}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => {
                        setSymbol(item.symbol);
                        setSymbolPickerOpen(false);
                      }}
                    >
                      <strong>{item.name}</strong>
                      <span>{item.symbol}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
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
            <label>
              每次拉取K线数量
              <input type="number" min={30} max={1000} value={marketLimit} onChange={(event) => setMarketLimit(Number(event.target.value))} />
            </label>
          </div>
          <button className="primary-action" disabled={loading} onClick={() => void pollMarket()}>{loading ? "拉取中..." : "获取实盘K线并扫描"}</button>
          {error && <div className="error-box">{error}</div>}
          <div className="progress-box">
            <div className="progress-meta">
              <span>{result ? "本次扫描完成" : "等待实盘拉取"}</span>
              <strong>{progress}%</strong>
            </div>
            <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
            {latestBar && <p>最新K线：{formatTime(latestBar.time)}，收盘 {formatPrice(latestBar.close)}，成交量 {latestBar.volume}</p>}
          </div>
        </aside>

        <section className="result-panel">
          <div className="panel-head">
            <div>
              <h2>实时K线结构</h2>
              <p>{result ? `${result.start_time} - ${result.end_time}` : "输入合约代码和周期后，点击获取实盘K线开始扫描。"} </p>
            </div>
            <span className="badge">{result?.symbol ?? symbol} / {result?.timeframe ?? timeframe}</span>
          </div>
          <KlineChartEcharts
            candles={result?.chart.candles ?? []}
            pivots={result?.chart.pivots ?? []}
            necklines={result?.chart.necklines ?? []}
            signals={selectedSignals}
            focusedSignal={focusedSignal}
          />

          <div className="signal-display-controls">
            <div>
              <strong>图上显示</strong>
              <span>{selectedCount} / {allSignals.length} 个信号</span>
            </div>
            <div className="signal-display-actions">
              <button type="button" className="compact-button" onClick={showAllSignals} disabled={allSignals.length === 0}>显示全部</button>
              <button type="button" className="compact-button muted-button" onClick={clearSelectedSignals} disabled={selectedCount === 0}>清空</button>
            </div>
          </div>

          <div className="signal-columns">
            <SignalGroup
              title="确认信号"
              signals={confirmed}
              empty="暂无确认头肩顶"
              selectedSignalKeys={selectedSignalKeys}
              onToggleSignal={toggleSignalSelection}
            />
            <SignalGroup
              title="疑似信号"
              signals={suspected}
              empty="暂无疑似结构"
              selectedSignalKeys={selectedSignalKeys}
              onToggleSignal={toggleSignalSelection}
            />
          </div>
        </section>
      </section>

      {configOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setConfigOpen(false)}>
          <section
            className="config-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="config-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-head">
              <div>
                <p className="eyebrow">Strategy</p>
                <h2 id="config-title">关键参数配置</h2>
              </div>
              <button className="icon-button" type="button" onClick={() => setConfigOpen(false)} aria-label="关闭配置">
                关闭
              </button>
            </div>
            <div className="config-scroll">
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
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setConfigOpen(false)}>保存配置</button>
            </div>
          </section>
        </div>
      )}
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

function SignalGroup({
  title,
  signals,
  empty,
  selectedSignalKeys,
  onToggleSignal,
}: {
  title: string;
  signals: Signal[];
  empty: string;
  selectedSignalKeys: Set<string>;
  onToggleSignal: (signal: Signal) => void;
}) {
  return (
    <div className="signal-group">
      <h3>{title}</h3>
      {signals.length === 0 ? <p className="empty">{empty}</p> : signals.map((signal, index) => {
        const key = signalKey(signal);
        const selected = selectedSignalKeys.has(key);
        return (
        <button
          type="button"
          className={`signal-card ${signal.confirmed ? "confirmed" : ""} ${selected ? "selected" : ""}`}
          key={`${signal.head.time}-${index}`}
          onClick={() => onToggleSignal(signal)}
          aria-pressed={selected}
        >
          <div className="signal-top">
            <strong>{signal.score}</strong>
            <span>{patternLabel(signal.pattern)} · {signal.confirmed ? "已确认" : "疑似"} · {selected ? "图上显示" : "点击显示"}</span>
          </div>
          <p>{translateResultText(signal.message)}</p>
          <div className="signal-times">
            <div><span>左肩</span><strong>{formatTime(signal.left_shoulder.time)}</strong></div>
            <div><span>左颈</span><strong>{formatTime(signal.left_neck.time)}</strong></div>
            <div><span>头部</span><strong>{formatTime(signal.head.time)}</strong></div>
            <div><span>右颈</span><strong>{formatTime(signal.right_neck.time)}</strong></div>
            <div><span>右肩</span><strong>{formatTime(signal.right_shoulder.time)}</strong></div>
          </div>
          <dl>
            <div><dt>左肩</dt><dd>{formatPrice(signal.left_shoulder.price)}</dd></div>
            <div><dt>头部</dt><dd>{formatPrice(signal.head.price)}</dd></div>
            <div><dt>右肩</dt><dd>{formatPrice(signal.right_shoulder.price)}</dd></div>
            <div><dt>颈线</dt><dd>{formatPrice(signal.neckline_price)}</dd></div>
          </dl>
          <ul>
            {signal.reasons.slice(0, 8).map((reason) => <li key={reason}>{translateResultText(reason)}</li>)}
          </ul>
        </button>
      );
      })}
    </div>
  );
}

function KlineChartEcharts({ candles, signals, focusedSignal }: {
  candles: Candle[];
  pivots: PivotPoint[];
  necklines: Neckline[];
  signals: Signal[];
  focusedSignal: Signal | null;
}) {
  const chartRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!chartRef.current || candles.length === 0) {
      return;
    }

    const chart = echarts.init(chartRef.current, "dark", {
      renderer: "canvas",
      devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      useDirtyRect: true,
    });
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
    const defaultStart = candles.length > 160 ? Math.max(0, 100 - (160 / candles.length) * 100) : 0;
    const focusZoom = focusedSignal ? calculateSignalZoom(focusedSignal, candles) : null;
    const start = focusZoom?.start ?? defaultStart;
    const end = focusZoom?.end ?? 100;
    const chartEl = chartRef.current;
    const useLongPressTooltip = window.matchMedia?.("(hover: none), (pointer: coarse)").matches ?? false;
    let zoomStart = start;
    let zoomEnd = end;

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
        triggerOn: useLongPressTooltip ? "none" : "mousemove",
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
        {
          type: "inside",
          xAxisIndex: [0, 1],
          start,
          end,
          zoomOnMouseWheel: true,
          moveOnMouseWheel: true,
          moveOnMouseMove: true,
          preventDefaultMouseMove: true,
          throttle: 80,
        },
        {
          type: "slider",
          xAxisIndex: [0, 1],
          bottom: 6,
          height: 18,
          realtime: false,
          brushSelect: false,
          borderColor: "rgba(148,163,184,0.18)",
          fillerColor: "rgba(121,183,164,0.16)",
          handleStyle: { color: "#79b7a4" },
          textStyle: { color: "#91a39a" },
        },
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

    chart.on("datazoom", () => {
      const option = chart.getOption();
      const dataZoom = Array.isArray(option.dataZoom) ? option.dataZoom[0] as { start?: number; end?: number } : null;
      zoomStart = Number(dataZoom?.start ?? zoomStart);
      zoomEnd = Number(dataZoom?.end ?? zoomEnd);
    });

    const showTooltipAt = (offsetX: number, offsetY: number) => {
      if (!chart.containPixel({ gridIndex: 0 }, [offsetX, offsetY]) && !chart.containPixel({ gridIndex: 1 }, [offsetX, offsetY])) {
        return;
      }
      const converted = chart.convertFromPixel({ xAxisIndex: 0 }, [offsetX, offsetY]);
      const rawIndex = Array.isArray(converted) ? converted[0] : converted;
      const dataIndex = Math.round(Number(rawIndex));
      if (!Number.isFinite(dataIndex)) {
        return;
      }
      chart.dispatchAction({
        type: "showTip",
        seriesIndex: 0,
        dataIndex: Math.max(0, Math.min(candles.length - 1, dataIndex)),
      });
    };

    let dragState: {
      pointerId: number;
      x: number;
      y: number;
      start: number;
      end: number;
      active: boolean;
    } | null = null;
    const activePointers = new Set<number>();
    let pendingZoom: { start: number; end: number } | null = null;
    let zoomFrame = 0;
    let longPressTimer = 0;
    let longPressStart: { pointerId: number; x: number; y: number } | null = null;

    const clampZoomStart = (value: number, span: number) => Math.max(0, Math.min(100 - span, value));
    const clearLongPress = () => {
      if (longPressTimer) {
        window.clearTimeout(longPressTimer);
        longPressTimer = 0;
      }
      longPressStart = null;
    };
    const getChartOffset = (event: PointerEvent) => {
      const rect = chartEl.getBoundingClientRect();
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      };
    };
    const flushZoom = () => {
      zoomFrame = 0;
      if (!pendingZoom) {
        return;
      }
      chart.dispatchAction({
        type: "dataZoom",
        dataZoomIndex: 0,
        start: pendingZoom.start,
        end: pendingZoom.end,
      });
      pendingZoom = null;
    };
    const scheduleZoom = (nextStart: number, nextEnd: number) => {
      pendingZoom = { start: nextStart, end: nextEnd };
      if (!zoomFrame) {
        zoomFrame = window.requestAnimationFrame(flushZoom);
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      activePointers.add(event.pointerId);
      if (activePointers.size > 1) {
        dragState = null;
        clearLongPress();
        return;
      }
      if (useLongPressTooltip && event.pointerType !== "mouse") {
        const offset = getChartOffset(event);
        longPressStart = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
        longPressTimer = window.setTimeout(() => {
          showTooltipAt(offset.x, offset.y);
          longPressTimer = 0;
          longPressStart = null;
        }, 560);
      }
      if (event.pointerType === "mouse" || candles.length <= 1) {
        return;
      }
      dragState = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        start: zoomStart,
        end: zoomEnd,
        active: false,
      };
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragState || dragState.pointerId !== event.pointerId) {
        return;
      }
      if (activePointers.size > 1) {
        dragState = null;
        clearLongPress();
        return;
      }
      const dx = event.clientX - dragState.x;
      const dy = event.clientY - dragState.y;
      if (
        longPressStart?.pointerId === event.pointerId
        && Math.hypot(event.clientX - longPressStart.x, event.clientY - longPressStart.y) > 10
      ) {
        clearLongPress();
      }
      if (!dragState.active) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) {
          return;
        }
        if (Math.abs(dx) <= Math.abs(dy)) {
          dragState = null;
          return;
        }
        dragState.active = true;
        chartEl.setPointerCapture?.(event.pointerId);
      }
      event.preventDefault();
      const span = Math.max(1, dragState.end - dragState.start);
      const shift = -(dx / Math.max(1, chartEl.clientWidth)) * span;
      const nextStart = clampZoomStart(dragState.start + shift, span);
      scheduleZoom(nextStart, nextStart + span);
    };
    const onPointerEnd = (event: PointerEvent) => {
      activePointers.delete(event.pointerId);
      if (longPressStart?.pointerId === event.pointerId) {
        clearLongPress();
      }
      if (dragState?.pointerId === event.pointerId) {
        chartEl.releasePointerCapture?.(event.pointerId);
        dragState = null;
      }
    };
    const onContextMenu = (event: MouseEvent) => {
      if (useLongPressTooltip) {
        event.preventDefault();
      }
    };
    chartEl.addEventListener("pointerdown", onPointerDown);
    chartEl.addEventListener("pointermove", onPointerMove);
    chartEl.addEventListener("pointerup", onPointerEnd);
    chartEl.addEventListener("pointercancel", onPointerEnd);
    chartEl.addEventListener("contextmenu", onContextMenu);

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartEl);
    return () => {
      chartEl.removeEventListener("pointerdown", onPointerDown);
      chartEl.removeEventListener("pointermove", onPointerMove);
      chartEl.removeEventListener("pointerup", onPointerEnd);
      chartEl.removeEventListener("pointercancel", onPointerEnd);
      chartEl.removeEventListener("contextmenu", onContextMenu);
      clearLongPress();
      if (zoomFrame) {
        window.cancelAnimationFrame(zoomFrame);
      }
      observer.disconnect();
      chart.dispose();
    };
  }, [candles, signals, focusedSignal]);

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

function patternLabel(pattern: Signal["pattern"]) {
  if (pattern === "inverse_head_shoulders") {
    return "反向头肩顶";
  }
  return "头肩顶";
}

function calculateChartNeckline(leftNeck: PivotPoint, rightNeck: PivotPoint, currentIndex: number) {
  if (leftNeck.index === rightNeck.index) {
    return rightNeck.price;
  }
  const slope = (rightNeck.price - leftNeck.price) / (rightNeck.index - leftNeck.index);
  return leftNeck.price + slope * (currentIndex - leftNeck.index);
}

function calculateSignalZoom(signal: Signal, candles: Candle[]) {
  const candleCount = candles.length;
  if (candleCount <= 0) {
    return { start: 0, end: 100 };
  }
  const breakIndex = signal.break_time
    ? candles.findIndex((candle) => candle.time === signal.break_time)
    : -1;
  const fromIndex = Math.max(0, signal.left_shoulder.index - 8);
  const toIndex = Math.min(candleCount - 1, Math.max(signal.right_shoulder.index, breakIndex) + 14);
  const minWindow = Math.min(candleCount, 36);
  const currentWindow = toIndex - fromIndex + 1;
  const extra = Math.max(0, minWindow - currentWindow);
  const paddedFrom = Math.max(0, fromIndex - Math.floor(extra / 2));
  const paddedTo = Math.min(candleCount - 1, toIndex + Math.ceil(extra / 2));
  return {
    start: (paddedFrom / candleCount) * 100,
    end: ((paddedTo + 1) / candleCount) * 100,
  };
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
