import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button as AntButton, Checkbox, ConfigProvider, Input, InputNumber, Select } from "antd";
import * as echarts from "echarts/core";
import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, MarkPointComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { createAlertFeedback, createWatchPoolItem, deleteAlertFeedback, deleteWatchPoolItem, disableAllWatchPoolItems, downloadWatchPoolImportTemplate, enableAllWatchPoolItems, getDefaultConfig, getHeadShouldersAlert, getMarketSettings, hideHeadShouldersAlert, importWatchPoolExcel, listAlertFeedbacks, listContracts, listHeadShouldersAlerts, listWatchPool, refreshContracts, scanMarket, scanWatchPoolOnce, updateContracts, updateWatchPoolItem } from "./api";
import type { AlertFeedback, Candle, ContractCenterItem, ContractCenterRefresh, HeadShouldersAlert, HeadShouldersAlertSummary, MarketSettings, Neckline, PivotPoint, ScanResponse, Signal, WatchPoolImportResult, WatchPoolItem as ApiWatchPoolItem } from "./types";
import "antd/dist/reset.css";
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
  "min_shoulder_to_head_height_ratio",
  "max_shoulder_diff_pct",
  "max_neck_diff_pct",
  "min_right_leg_to_left_leg_ratio",
  "max_right_leg_to_left_leg_ratio",
  "min_head_to_right_neck_to_left_neck_to_head_ratio",
  "max_head_to_right_neck_to_left_neck_to_head_ratio",
  "min_shoulder_to_neck_height",
  "neckline_break_pct",
  "max_bars_after_right_shoulder",
  "max_signal_age_bars",
  "min_score_to_alert",
];

const booleanFields = [
  "require_head_beyond_shoulders_and_necks",
  "require_shoulders_between_opposite_neck_and_head",
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

const MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache_v7";
const LEGACY_MARKET_SCAN_CACHE_KEY = "lh_demo_market_scan_cache";
const MARKET_SCAN_CACHE_VERSION = 7;
const MANUAL_MARKET_SCAN_OVERRIDES = {
  max_signal_age_bars: 0,
};
const EMPTY_CANDLES: Candle[] = [];
const EMPTY_PIVOTS: PivotPoint[] = [];
const EMPTY_NECKLINES: Neckline[] = [];
const DEFAULT_TRADING_SESSIONS = "day,night";
const TIMEFRAME_OPTIONS = [
  { value: "1m", label: "1分钟" },
  { value: "3m", label: "3分钟" },
  { value: "5m", label: "5分钟" },
  { value: "15m", label: "15分钟" },
  { value: "30m", label: "30分钟" },
  { value: "1h", label: "1小时" },
  { value: "1d", label: "日线" },
];
const antTheme = {
  token: {
    colorPrimary: "#0066cc",
    colorInfo: "#0066cc",
    colorText: "#1d1d1f",
    colorTextSecondary: "#7a7a7a",
    colorBorder: "#e0e0e0",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    borderRadius: 11,
    borderRadiusLG: 18,
    controlHeight: 44,
    fontFamily: '"SF Pro Text", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei UI", sans-serif',
  },
  components: {
    Button: {
      borderRadius: 999,
      controlHeight: 44,
      fontWeight: 400,
      primaryShadow: "none",
    },
    Input: {
      borderRadius: 999,
      activeShadow: "0 0 0 3px rgba(0, 113, 227, 0.14)",
    },
    InputNumber: {
      borderRadius: 999,
      activeShadow: "0 0 0 3px rgba(0, 113, 227, 0.14)",
    },
    Select: {
      borderRadius: 999,
      optionSelectedBg: "rgba(0, 102, 204, 0.08)",
    },
  },
};

type CachedMarketScan = {
  version: number;
  savedAt: string;
  limit: number;
  result: ScanResponse;
};

type WatchPoolItem = {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  enabled: boolean;
  monitorMinutes: number;
  tradingSessions: string;
  minHeadToNeckHeight: number;
  minShoulderToNeckHeight: number;
  createdAt: string;
};

type WatchPoolDraft = Omit<WatchPoolItem, "id" | "createdAt">;
type FeedbackTab = "alerts" | "current" | "feedbacks";
type DetailSource =
  | { kind: "alert"; alert: HeadShouldersAlert }
  | { kind: "current"; signal: Signal }
  | null;
type TradingSessionKey = "day" | "night";
type ContractSymbolOption = {
  value: string;
  label: React.ReactNode;
  searchText: string;
  name: string;
};

const watchPoolImportDemo = [
  ["品种名称", "监控品种", "监控周期", "检测时长", "头部到颈线最小高度", "颈到肩最小价差", "交易时间段", "监控开关"],
  ["螺纹钢", "SHFE.rb2605", "1m", "30", "0", "0", "day,night", "开启"],
  ["热卷", "SHFE.hc2610", "3m", "60", "8", "4", "day", "开启"],
];

const tradingSessionOptions: Array<{ key: TradingSessionKey; label: string; range: string }> = [
  { key: "day", label: "白天", range: "09:00-11:30 / 13:30-15:00" },
  { key: "night", label: "夜间", range: "21:00-23:00" },
];

const emptyWatchDraft: WatchPoolDraft = {
  name: "",
  symbol: "",
  timeframe: "1m",
  enabled: true,
  monitorMinutes: 30,
  tradingSessions: DEFAULT_TRADING_SESSIONS,
  minHeadToNeckHeight: 0,
  minShoulderToNeckHeight: 0,
};

function normalizeTradingSessions(value: string) {
  const keys = value.split(",").map((item) => item.trim()).filter((item): item is TradingSessionKey => item === "day" || item === "night");
  return Array.from(new Set(keys)).join(",");
}

function tradingSessionLabel(value: string) {
  const keys = normalizeTradingSessions(value || DEFAULT_TRADING_SESSIONS).split(",");
  return tradingSessionOptions.filter((item) => keys.includes(item.key)).map((item) => item.label).join("、") || "未选择";
}

function mapWatchPoolItem(item: ApiWatchPoolItem): WatchPoolItem {
  return {
    id: item.id,
    name: item.name,
    symbol: item.symbol,
    timeframe: item.timeframe,
    enabled: item.enabled,
    monitorMinutes: item.monitor_minutes,
    tradingSessions: item.trading_sessions || DEFAULT_TRADING_SESSIONS,
    minHeadToNeckHeight: item.min_head_to_neck_height ?? 0,
    minShoulderToNeckHeight: item.min_shoulder_to_neck_height ?? 0,
    createdAt: item.created_at ? formatAlertTime(item.created_at) : "--",
  };
}

function App() {
  const [symbol, setSymbol] = useState("");
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
  const [watchPool, setWatchPool] = useState<WatchPoolItem[]>([]);
  const [watchDraft, setWatchDraft] = useState<WatchPoolDraft>(emptyWatchDraft);
  const [editingWatchId, setEditingWatchId] = useState<string | null>(null);
  const [watchEditorOpen, setWatchEditorOpen] = useState(false);
  const [watchImportOpen, setWatchImportOpen] = useState(false);
  const [watchImportResult, setWatchImportResult] = useState<WatchPoolImportResult | null>(null);
  const [watchImporting, setWatchImporting] = useState(false);
  const [watchTogglePendingIds, setWatchTogglePendingIds] = useState<Set<string>>(new Set());
  const [feedbackTab, setFeedbackTab] = useState<FeedbackTab>("alerts");
  const [monitorAlerts, setMonitorAlerts] = useState<HeadShouldersAlertSummary[]>([]);
  const [feedbacks, setFeedbacks] = useState<AlertFeedback[]>([]);
  const [feedbackTarget, setFeedbackTarget] = useState<HeadShouldersAlertSummary | null>(null);
  const [feedbackListOpen, setFeedbackListOpen] = useState(false);
  const [contractCenterOpen, setContractCenterOpen] = useState(false);
  const [contracts, setContracts] = useState<ContractCenterItem[]>([]);
  const [contractRefresh, setContractRefresh] = useState<ContractCenterRefresh | null>(null);
  const [contractLoading, setContractLoading] = useState(false);
  const [contractUpdating, setContractUpdating] = useState(false);
  const [contractMessage, setContractMessage] = useState<string | null>(null);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [selectedFeedbackId, setSelectedFeedbackId] = useState<string | null>(null);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [detailSource, setDetailSource] = useState<DetailSource>(null);
  const [scoreDetailSignal, setScoreDetailSignal] = useState<Signal | null>(null);
  const [monitorScrollTargetSymbol, setMonitorScrollTargetSymbol] = useState<string | null>(null);
  const seenSignalKeys = useRef<Set<string>>(new Set());
  const feedbackPanelRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!symbol) {
      return;
    }
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
    listWatchPool()
      .then((items) => setWatchPool(items.map(mapWatchPoolItem)))
      .catch((err) => setError(`检测池数据库读取失败：${err.message}`));
  }, []);

  useEffect(() => {
    listContracts()
      .then(setContracts)
      .catch((err) => setError(err instanceof Error ? err.message : "合约列表读取失败"));
  }, []);

  useEffect(() => {
    refreshMonitorAlerts();
    refreshFeedbacks();
    const timer = window.setInterval(() => {
      void refreshMonitorAlerts();
    }, 10000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    document.body.classList.toggle("modal-open", configOpen || watchEditorOpen || detailSource !== null || scoreDetailSignal !== null || feedbackTarget !== null || feedbackListOpen || contractCenterOpen);
    return () => document.body.classList.remove("modal-open");
  }, [configOpen, watchEditorOpen, detailSource, scoreDetailSignal, feedbackTarget, feedbackListOpen, contractCenterOpen]);

  function scrollToPanel(ref: React.RefObject<HTMLElement | null>) {
    window.setTimeout(() => {
      ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

  async function pollMarket() {
    if (!symbol) {
      setError("请先选择监控品种");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await requestBrowserNotification();
      const response = await scanMarket(symbol, timeframe, marketLimit, {
        ...config,
        ...MANUAL_MARKET_SCAN_OVERRIDES,
      });
      applyScanResponse(response);
      saveMarketScanCache(response, marketLimit);
      setMarketLastFetch(`接口 ${new Date().toLocaleTimeString()}`);
      pushNewAlerts(response.signals);
      setFeedbackTab("current");
      scrollToPanel(feedbackPanelRef);
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
      setError("本地缓存写入失败，请检查浏览器存储权限");
    }
  }

  function pushNewAlerts(signals: Signal[]) {
    for (const signal of signals) {
      const key = signalKey(signal);
      if (seenSignalKeys.current.has(key)) {
        continue;
      }
      seenSignalKeys.current.add(key);
      const title = `${patternLabel(signal.pattern)}${alertTypeLabel(signal.alert_type)}`;
      const message = translateResultText(signal.message);
      sendBrowserNotification(title, message);
    }
  }

  const currentSignals = useMemo(() => result?.signals ?? [], [result]);
  const visibleMonitorAlerts = monitorAlerts;
  const confirmed = useMemo(() => currentSignals.filter((signal) => signal.confirmed), [currentSignals]);
  const suspected = useMemo(() => currentSignals.filter((signal) => !signal.confirmed), [currentSignals]);
  const selectedSignals = useMemo(
    () => currentSignals.filter((signal) => selectedSignalKeys.has(signalKey(signal))),
    [currentSignals, selectedSignalKeys],
  );
  const focusedSignal = useMemo(
    () => selectedSignals.find((signal) => signalKey(signal) === focusedSignalKey) ?? null,
    [focusedSignalKey, selectedSignals],
  );
  const currentSignalKeys = useMemo(() => currentSignals.map(signalKey), [currentSignals]);
  const selectedCount = selectedSignals.length;
  const contractSymbolOptions = useMemo<ContractSymbolOption[]>(() => {
    const source = contracts.length > 0
      ? contracts.map((item) => ({ symbol: item.symbol, name: item.name || item.symbol }))
      : futuresSymbolOptions;
    return source.map((item) => ({
      value: item.symbol,
      label: (
        <span className="contract-option-label">
          <strong>{item.symbol}</strong>
          <span>{item.name}</span>
        </span>
      ),
      searchText: `${item.symbol} ${item.name}`,
      name: item.name,
    }));
  }, [contracts]);

  useEffect(() => {
    if (contractSymbolOptions.length === 0) {
      return;
    }
    if (!symbol || !contractSymbolOptions.some((item) => item.value === symbol)) {
      setSymbol(contractSymbolOptions[0].value);
    }
  }, [contractSymbolOptions, symbol]);
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
    setSelectedSignalKeys(new Set(currentSignalKeys));
    setFocusedSignalKey(currentSignalKeys[0] ?? null);
  }

  function clearSelectedSignals() {
    setSelectedSignalKeys(new Set());
    setFocusedSignalKey(null);
  }

  async function refreshMonitorAlerts() {
    try {
      const alerts = await listHeadShouldersAlerts(100);
      setMonitorAlerts(alerts);
    } catch (err) {
      setError(err instanceof Error ? `监控消息读取失败：${err.message}` : "监控消息读取失败");
    }
  }

  async function refreshFeedbacks() {
    try {
      setFeedbacks(await listAlertFeedbacks(100));
    } catch (err) {
      setError(err instanceof Error ? `反馈列表读取失败：${err.message}` : "反馈列表读取失败");
    }
  }

  function startCreateWatch() {
    setEditingWatchId(null);
    setWatchDraft(emptyWatchDraft);
    setWatchEditorOpen(true);
  }

  function startEditWatch(item: WatchPoolItem) {
    setEditingWatchId(item.id);
    setWatchDraft({
      name: item.name,
      symbol: item.symbol,
      timeframe: item.timeframe,
      enabled: item.enabled,
      monitorMinutes: item.monitorMinutes,
      tradingSessions: item.tradingSessions,
      minHeadToNeckHeight: item.minHeadToNeckHeight,
      minShoulderToNeckHeight: item.minShoulderToNeckHeight,
    });
    setWatchEditorOpen(true);
  }

  function resetWatchDraft() {
    setEditingWatchId(null);
    setWatchDraft(emptyWatchDraft);
    setWatchEditorOpen(false);
  }

  async function saveWatchPoolItem() {
    const normalizedSymbol = watchDraft.symbol.trim();
    if (!normalizedSymbol) {
      setError("监控品种不能为空");
      return;
    }
    const selectedOption = contractSymbolOptions.find((item) => item.value.toLowerCase() === normalizedSymbol.toLowerCase());
    if (!selectedOption) {
      setError("监控品种必须从合约列表中选择");
      return;
    }
    const normalizedName = selectedOption.name.trim();
    const normalizedTradingSessions = normalizeTradingSessions(watchDraft.tradingSessions);
    if (!normalizedTradingSessions) {
      setError("请选择交易时间段");
      return;
    }
    const payload = {
      name: normalizedName,
      symbol: normalizedSymbol,
      timeframe: watchDraft.timeframe,
      enabled: watchDraft.enabled,
      monitor_minutes: Math.max(1, Number(watchDraft.monitorMinutes) || 1),
      trading_sessions: normalizedTradingSessions,
      min_head_to_neck_height: Math.max(0, Number(watchDraft.minHeadToNeckHeight) || 0),
      min_shoulder_to_neck_height: Math.max(0, Number(watchDraft.minShoulderToNeckHeight) || 0),
    };
    try {
      const saved = editingWatchId
        ? await updateWatchPoolItem(editingWatchId, payload)
        : await createWatchPoolItem(payload);
      const nextItem = mapWatchPoolItem(saved);
      setWatchPool((items) => {
        if (!editingWatchId) {
          return [nextItem, ...items];
        }
        return items.map((item) => item.id === editingWatchId ? nextItem : item);
      });
      resetWatchDraft();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池保存失败");
    }
  }

  async function removeWatchPoolItem(id: string) {
    try {
      await deleteWatchPoolItem(id);
      setWatchPool((items) => items.filter((item) => item.id !== id));
      if (editingWatchId === id) {
        resetWatchDraft();
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池删除失败");
    }
  }

  async function downloadWatchTemplate() {
    try {
      await downloadWatchPoolImportTemplate();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池示例下载失败");
    }
  }

  async function importWatchPoolFile(file: File) {
    setWatchImporting(true);
    try {
      const result = await importWatchPoolExcel(file);
      setWatchImportResult(result);
      const items = await listWatchPool();
      setWatchPool(items.map(mapWatchPoolItem));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池批量导入失败");
    } finally {
      setWatchImporting(false);
    }
  }

  async function toggleWatchPoolEnabled(item: WatchPoolItem) {
    if (watchTogglePendingIds.has(item.id)) {
      return;
    }
    setWatchTogglePendingIds((ids) => new Set(ids).add(item.id));
    try {
      const saved = await updateWatchPoolItem(item.id, {
        name: item.name,
        symbol: item.symbol,
        timeframe: item.timeframe,
        enabled: !item.enabled,
        monitor_minutes: item.monitorMinutes,
        trading_sessions: normalizeTradingSessions(item.tradingSessions) || DEFAULT_TRADING_SESSIONS,
        min_head_to_neck_height: item.minHeadToNeckHeight,
        min_shoulder_to_neck_height: item.minShoulderToNeckHeight,
      });
      const nextItem = mapWatchPoolItem(saved);
      setWatchPool((items) => items.map((current) => current.id === item.id ? nextItem : current));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "监控状态更新失败");
    } finally {
      setWatchTogglePendingIds((ids) => {
        const next = new Set(ids);
        next.delete(item.id);
        return next;
      });
    }
  }

  async function enableAllWatchPool() {
    try {
      const items = await enableAllWatchPoolItems();
      setWatchPool(items.map(mapWatchPoolItem));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池一键开启失败");
    }
  }

  async function disableAllWatchPool() {
    try {
      const items = await disableAllWatchPoolItems();
      setWatchPool(items.map(mapWatchPoolItem));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池一键关闭失败");
    }
  }

  function focusCurrentSignal(signal: Signal) {
    const key = signalKey(signal);
    setSelectedSignalKeys(new Set([key]));
    setFocusedSignalKey(key);
  }

  function selectCurrentSignal(signal: Signal) {
    focusCurrentSignal(signal);
    setDetailSource({ kind: "current", signal });
  }

  function focusWatchPoolMessages(item: WatchPoolItem) {
    setFeedbackTab("alerts");
    setMonitorScrollTargetSymbol(item.symbol);
    scrollToPanel(feedbackPanelRef);
  }

  function applyAlertResult(fullAlert: HeadShouldersAlert) {
    setSelectedAlertId(fullAlert.id);
    setSelectedFeedbackId(null);
    setResult((current) => ({
      symbol: fullAlert.symbol,
      timeframe: fullAlert.timeframe,
      rows: fullAlert.chart_payload.candles.length,
      start_time: fullAlert.chart_payload.candles[0]?.time ?? current?.start_time ?? null,
      end_time: fullAlert.chart_payload.candles[fullAlert.chart_payload.candles.length - 1]?.time ?? current?.end_time ?? null,
      config: current?.config ?? {},
      signals: [fullAlert.signal_payload],
      chart: fullAlert.chart_payload,
    }));
    const key = signalKey(fullAlert.signal_payload);
    setSelectedSignalKeys(new Set([key]));
    setFocusedSignalKey(key);
  }

  async function selectMonitorAlert(alert: HeadShouldersAlertSummary) {
    try {
      applyAlertResult(await getHeadShouldersAlert(alert.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "监控消息详情读取失败");
    }
  }

  async function openMonitorAlertDetail(alert: HeadShouldersAlertSummary) {
    try {
      const fullAlert = await getHeadShouldersAlert(alert.id);
      applyAlertResult(fullAlert);
      setDetailSource({ kind: "alert", alert: fullAlert });
    } catch (err) {
      setError(err instanceof Error ? err.message : "监控消息详情读取失败");
    }
  }

  async function hideMonitorAlert(alertId: string) {
    try {
      await hideHeadShouldersAlert(alertId);
      setMonitorAlerts((items) => items.filter((item) => item.id !== alertId));
      if (selectedAlertId === alertId) {
        setSelectedAlertId(null);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "监控消息关闭失败");
    }
  }

  function startAlertFeedback(alert: HeadShouldersAlertSummary) {
    setFeedbackTarget(alert);
    setFeedbackNote("");
  }

  function selectFeedback(feedback: AlertFeedback) {
    setSelectedFeedbackId(feedback.id);
    setSelectedAlertId(null);
    setResult((current) => ({
      symbol: feedback.symbol,
      timeframe: feedback.timeframe,
      rows: feedback.chart_payload.candles.length,
      start_time: feedback.chart_payload.candles[0]?.time ?? current?.start_time ?? null,
      end_time: feedback.chart_payload.candles[feedback.chart_payload.candles.length - 1]?.time ?? current?.end_time ?? null,
      config: current?.config ?? {},
      signals: [feedback.signal_payload],
      chart: feedback.chart_payload,
    }));
    const key = signalKey(feedback.signal_payload);
    setSelectedSignalKeys(new Set([key]));
    setFocusedSignalKey(key);
  }

  async function saveAlertFeedback() {
    if (!feedbackTarget) return;
    try {
      const saved = await createAlertFeedback(feedbackTarget.id, feedbackNote.trim());
      setFeedbacks((items) => [saved, ...items]);
      setFeedbackTarget(null);
      setFeedbackNote("");
      setFeedbackTab("feedbacks");
      selectFeedback(saved);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈保存失败");
    }
  }

  async function removeFeedback(id: string) {
    try {
      await deleteAlertFeedback(id);
      setFeedbacks((items) => items.filter((item) => item.id !== id));
      if (selectedFeedbackId === id) setSelectedFeedbackId(null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈删除失败");
    }
  }

  async function scanWatchPoolNow() {
    try {
      await scanWatchPoolOnce(marketLimit);
      await refreshMonitorAlerts();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测池扫描失败");
    }
  }

  async function openContractCenter() {
    setContractCenterOpen(true);
    setContractMessage(null);
    try {
      setContracts(await listContracts());
    } catch (err) {
      setError(err instanceof Error ? err.message : "合约中心读取失败");
    }
  }

  async function refreshContractCenter() {
    setContractLoading(true);
    setContractMessage(null);
    try {
      const response = await refreshContracts();
      setContractRefresh(response);
      setContractMessage(
        response.new_count > 0 || response.stale_count > 0
          ? `发现 ${response.new_count} 个新增合约、${response.stale_count} 个失效合约，确认后同步数据库。`
          : "当前数据库已是最新。",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "合约刷新失败");
    } finally {
      setContractLoading(false);
    }
  }

  async function applyContractUpdates() {
    if (!contractRefresh || (contractRefresh.new_symbols.length === 0 && contractRefresh.stale_symbols.length === 0)) return;
    setContractUpdating(true);
    setContractMessage(null);
    try {
      const response = await updateContracts({
        symbols: contractRefresh.new_symbols,
        latest_symbols: contractRefresh.latest_symbols,
        exchanges: contractRefresh.exchanges,
        prune_missing: true,
      });
      setContracts(response.items);
      setContractRefresh(null);
      setContractMessage(`已新增 ${response.inserted} 个合约，移除 ${response.removed} 个失效合约。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "合约更新失败");
    } finally {
      setContractUpdating(false);
    }
  }

  return (
    <ConfigProvider theme={antTheme}>
    <main className="app-shell">
      <header className="terminal-header">
        <div className="terminal-title">
          <strong>K线头肩形态检测</strong>
          <span>交易分析工作台</span>
        </div>
        <div className="terminal-status">
          <AntButton className="header-feedback-button" onClick={() => setFeedbackListOpen(true)}>&#21453;&#39304;&#21015;&#34920;</AntButton>
          <AntButton className="header-feedback-button" onClick={() => void openContractCenter()}>合约中心</AntButton>
          <span>{marketSettings?.provider ?? "Market"}</span>
          <span>{result?.symbol ?? symbol} / {result?.timeframe ?? timeframe}</span>
          <span>{marketLastFetch ?? "等待扫描"}</span>
        </div>
      </header>
      <section className="trading-desk">
        <aside className="control-panel">
          <div className="control-head">
            <div>
              <p className="eyebrow">Kline Config</p>
              <h2>K线图配置</h2>
            </div>
            <AntButton className="icon-button" onClick={() => setConfigOpen(true)} aria-label="打开配置">
              策略参数
            </AntButton>
          </div>
          <div className="progress-box">
            <div className="progress-meta">
              <span>行情接口</span>
              <strong>{marketSettings?.api_key_set === "是" ? "已配置" : "未配置"}</strong>
            </div>
            <p>{marketSettings?.provider ?? "行情源"}：{marketSettings?.base_url ?? "未知"}</p>
            {marketLastFetch && <p>最近拉取：{marketLastFetch}</p>}
          </div>
          <div className="market-form">
            <label>
              监控品种
              <Select
                showSearch
                value={symbol}
                options={contractSymbolOptions}
                optionFilterProp="label"
                placeholder="输入代码或名称搜索"
                onChange={setSymbol}
                optionLabelProp="value"
                filterOption={(input, option) => String(option?.searchText ?? "").toLowerCase().includes(input.toLowerCase())}
              />
            </label>
            <label>
              监控周期
              <Select value={timeframe} onChange={setTimeframe} options={TIMEFRAME_OPTIONS} />
            </label>
            <label>
              拉取K线数量
              <InputNumber min={30} max={1000} value={marketLimit} onChange={(value) => setMarketLimit(Number(value) || 30)} />
            </label>
          </div>
          <AntButton type="primary" className="primary-action" loading={loading} disabled={loading} onClick={() => void pollMarket()}>{loading ? "扫描中..." : "获取K线数据"}</AntButton>
          {error && <div className="error-box">{error}</div>}
          <div className="progress-box">
            <div className="progress-meta">
              <span>{result ? "本次扫描完成" : "等待扫描"}</span>
              <strong>{progress}%</strong>
            </div>
            <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>
            {latestBar && <p>最新K线：{formatTime(latestBar.time)}，收盘 {formatPrice(latestBar.close)}，成交量 {latestBar.volume}</p>}
          </div>
        </aside>

        <section className="main-stage">
          <section className="result-panel chart-panel">
            <div className="panel-head">
              <div>
                <h2>实时K线结构</h2>
                <p>{result ? `${result.start_time} - ${result.end_time}` : "选择左侧配置后获取K线，点击右侧消息可定位头肩顶区间。"} </p>
              </div>
              <span className="badge">{result?.symbol ?? symbol} / {result?.timeframe ?? timeframe}</span>
            </div>
            <KlineChartEcharts
              candles={result?.chart.candles ?? EMPTY_CANDLES}
              pivots={result?.chart.pivots ?? EMPTY_PIVOTS}
              necklines={result?.chart.necklines ?? EMPTY_NECKLINES}
              signals={selectedSignals}
              focusedSignal={focusedSignal}
            />
            <div className="signal-display-controls">
              <div>
                <strong>图上显示</strong>
                <span>{selectedCount} / {currentSignals.length} 条当前图结果</span>
              </div>
              <div className="signal-display-actions">
                <AntButton className="compact-button" onClick={showAllSignals} disabled={currentSignals.length === 0}>显示全部</AntButton>
                <AntButton className="compact-button muted-button" onClick={clearSelectedSignals} disabled={selectedCount === 0}>清空</AntButton>
              </div>
            </div>
          </section>

        <WatchPool
          items={watchPool}
          draft={watchDraft}
          contractOptions={contractSymbolOptions}
          editingId={editingWatchId}
        editorOpen={watchEditorOpen}
        onDraftChange={setWatchDraft}
        onNew={startCreateWatch}
        onSave={saveWatchPoolItem}
        onCancel={resetWatchDraft}
        onEdit={startEditWatch}
          onDelete={removeWatchPoolItem}
          onToggleEnabled={toggleWatchPoolEnabled}
          onFocusMessages={focusWatchPoolMessages}
          onEnableAll={enableAllWatchPool}
          onDisableAll={disableAllWatchPool}
          onScanNow={() => void scanWatchPoolNow()}
          onDownloadTemplate={() => void downloadWatchTemplate()}
        onImportFile={(file) => void importWatchPoolFile(file)}
        importOpen={watchImportOpen}
        onImportOpen={() => setWatchImportOpen(true)}
        onImportClose={() => setWatchImportOpen(false)}
          importResult={watchImportResult}
          importing={watchImporting}
          togglePendingIds={watchTogglePendingIds}
        />
        </section>

        <aside className="feedback-panel" ref={feedbackPanelRef}>
          <FeedbackTabs
            activeTab={feedbackTab}
            onTabChange={setFeedbackTab}
            monitorAlerts={visibleMonitorAlerts}
            currentSignals={currentSignals}
            selectedAlertId={selectedAlertId}
            selectedSignalKey={focusedSignalKey}
            feedbacks={feedbacks}
            selectedFeedbackId={selectedFeedbackId}
            onSelectAlert={(alert) => void selectMonitorAlert(alert)}
            onOpenAlertDetail={(alert) => void openMonitorAlertDetail(alert)}
            onOpenScoreDetail={(signal) => setScoreDetailSignal(signal)}
            onHideAlert={(alertId) => void hideMonitorAlert(alertId)}
            onFeedbackAlert={startAlertFeedback}
            monitorScrollTargetSymbol={monitorScrollTargetSymbol}
            onMonitorScrollComplete={() => setMonitorScrollTargetSymbol(null)}
            onFocusCurrentSignal={focusCurrentSignal}
            onSelectCurrentSignal={selectCurrentSignal}
            onOpenCurrentScoreDetail={(signal) => setScoreDetailSignal(signal)}
            onSelectFeedback={selectFeedback}
            onDeleteFeedback={(id) => void removeFeedback(id)}
          />
        </aside>
      </section>

      {detailSource && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setDetailSource(null)}>
          <section
            className="detail-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="detail-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <SignalDetail
              signal={detailSource.kind === "alert" ? detailSource.alert.signal_payload : detailSource.signal}
              sourceLabel={detailSource.kind === "alert" ? "监控消息" : "当前图结果"}
              titleId="detail-title"
              onOpenScoreDetail={(signal) => setScoreDetailSignal(signal)}
              onClose={() => setDetailSource(null)}
            />
          </section>
        </div>
      )}

      {scoreDetailSignal && (
        <ScoreDetailModal signal={scoreDetailSignal} onClose={() => setScoreDetailSignal(null)} />
      )}

      {feedbackTarget && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setFeedbackTarget(null)}>
          <section
            className="feedback-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feedback-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <button type="button" className="modal-close-button" onClick={() => setFeedbackTarget(null)} aria-label="关闭反馈弹框">&#20851;&#38381;</button>
            <div className="modal-head">
              <div>
                <p className="eyebrow">&#21453;&#39304;</p>
                <h2 id="feedback-title">&#28155;&#21152;&#21453;&#39304;</h2>
              </div>
            </div>
            <div className="feedback-target">
              <strong>{feedbackTarget.symbol} / {feedbackTarget.timeframe}</strong>
              <span>{patternLabel(feedbackTarget.pattern)} &middot; {alertTypeLabel(feedbackTarget.alert_type)} &middot; &#35780;&#20998; {feedbackTarget.score}</span>
            </div>
            <label className="feedback-note-field">
              &#21453;&#39304;&#20449;&#24687;
              <Input.TextArea value={feedbackNote} onChange={(event) => setFeedbackNote(event.target.value)} maxLength={2000} placeholder="请输入反馈信息，例如误报原因、确认结果或后续处理记录" showCount />
            </label>
            <div className="modal-actions">
              <AntButton className="muted-button" onClick={() => setFeedbackTarget(null)}>&#21462;&#28040;</AntButton>
              <AntButton type="primary" onClick={() => void saveAlertFeedback()}>&#20445;&#23384;&#21453;&#39304;</AntButton>
            </div>
          </section>
        </div>
      )}

      {feedbackListOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setFeedbackListOpen(false)}>
          <section
            className="feedback-modal feedback-list-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feedback-list-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <button type="button" className="modal-close-button" onClick={() => setFeedbackListOpen(false)} aria-label="关闭反馈列表">&#20851;&#38381;</button>
            <div className="modal-head">
              <div>
                <p className="eyebrow">&#21453;&#39304;</p>
                <h2 id="feedback-list-title">&#21453;&#39304;&#21015;&#34920;</h2>
              </div>
            </div>
            <FeedbackFeed
              feedbacks={feedbacks}
              selectedId={selectedFeedbackId}
              onSelect={(feedback) => { selectFeedback(feedback); setFeedbackListOpen(false); }}
              onDelete={(id) => void removeFeedback(id)}
            />
          </section>
        </div>
      )}

      {contractCenterOpen && (
        <ContractCenterModal
          contracts={contracts}
          refreshState={contractRefresh}
          loading={contractLoading}
          updating={contractUpdating}
          message={contractMessage}
          onRefresh={() => void refreshContractCenter()}
              onUpdate={() => void applyContractUpdates()}
          onClose={() => setContractCenterOpen(false)}
        />
      )}

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
              <AntButton className="icon-button" onClick={() => setConfigOpen(false)} aria-label="关闭配置">
                关闭
              </AntButton>
            </div>
            <div className="config-scroll">
              <div className="config-grid">
                {numericFields.map((field) => (
                  <label key={field}>
                    {fieldLabel(field)}
                    <InputNumber
                      step={0.001}
                      value={typeof config[field] === "number" ? Number(config[field]) : null}
                      onChange={(value) => setConfig((prev) => ({ ...prev, [field]: Number(value) || 0 }))}
                    />
                  </label>
                ))}
              </div>
              {booleanFields.length > 0 && (
                <div className="switch-list">
                  {booleanFields.map((field) => (
                    <label key={field} className="switch-row">
                      <Checkbox
                        checked={Boolean(config[field])}
                        onChange={(event) => setConfig((prev) => ({ ...prev, [field]: event.target.checked }))}
                      />
                      {fieldLabel(field)}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="modal-actions">
              <AntButton type="primary" onClick={() => setConfigOpen(false)}>保存配置</AntButton>
            </div>
          </section>
        </div>
      )}
    </main>
    </ConfigProvider>
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

const WatchPool = React.forwardRef<HTMLElement, {
  items: WatchPoolItem[];
  draft: WatchPoolDraft;
  contractOptions: ContractSymbolOption[];
  editingId: string | null;
  editorOpen: boolean;
  onDraftChange: React.Dispatch<React.SetStateAction<WatchPoolDraft>>;
  onNew: () => void;
  onSave: () => void;
  onCancel: () => void;
  onEdit: (item: WatchPoolItem) => void;
  onDelete: (id: string) => void;
  onToggleEnabled: (item: WatchPoolItem) => void;
  onEnableAll: () => void;
  onDisableAll: () => void;
  onDownloadTemplate: () => void;
  onImportFile: (file: File) => void;
  importOpen: boolean;
  onImportOpen: () => void;
  onImportClose: () => void;
  importResult: WatchPoolImportResult | null;
  importing: boolean;
  togglePendingIds: Set<string>;
  onScanNow: () => void;
  onFocusMessages: (item: WatchPoolItem) => void;
}>(function WatchPool({
  items,
  draft,
  contractOptions,
  editingId,
  editorOpen,
  onDraftChange,
  onNew,
  onSave,
  onCancel,
  onEdit,
  onDelete,
  onToggleEnabled,
  onEnableAll,
  onDisableAll,
  onDownloadTemplate,
  onImportFile,
  importOpen,
  onImportOpen,
  onImportClose,
  importResult,
  importing,
  togglePendingIds,
  onScanNow,
  onFocusMessages,
}, ref) {
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const groupedItems = [
    { key: "1m", title: "1分钟检测池", items: items.filter((item) => item.timeframe === "1m") },
    { key: "3m", title: "3分钟检测池", items: items.filter((item) => item.timeframe === "3m") },
    { key: "5m", title: "5分钟检测池", items: items.filter((item) => item.timeframe === "5m") },
    { key: "other", title: "其他检测池", items: items.filter((item) => item.timeframe !== "1m" && item.timeframe !== "3m" && item.timeframe !== "5m") },
  ];
  const enabledCount = items.filter((item) => item.enabled).length;
  const allEnabled = items.length > 0 && enabledCount === items.length;
  const allDisabled = enabledCount === 0;
  const selectedDraftOption = contractOptions.find((item) => item.value.toLowerCase() === draft.symbol.trim().toLowerCase());
  const draftName = selectedDraftOption?.name ?? draft.name;

  const renderPoolCard = (item: WatchPoolItem) => {
    const togglePending = togglePendingIds.has(item.id);
    return (
    <article
      className="pool-card"
      key={item.id}
      onClick={() => onFocusMessages(item)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onFocusMessages(item);
        }
      }}
    >
      <div className="pool-card-top">
        <div>
          <strong>{item.name}</strong>
          <small>{item.symbol}</small>
        </div>
        <button
          type="button"
          className={`${item.enabled ? "pool-enable-switch on" : "pool-enable-switch"} ${togglePending ? "loading" : ""}`}
          aria-label={item.enabled ? "关闭监控" : "开启监控"}
          aria-pressed={item.enabled}
          disabled={togglePending}
          onClick={(event) => {
            event.stopPropagation();
            onToggleEnabled(item);
          }}
        >
          <span />
          {togglePending && <i aria-hidden="true" />}
        </button>
      </div>
      <div className="pool-card-meta">
        <span>周期 <b>{item.timeframe}</b></span>
        <span>时长 <b>{item.monitorMinutes} 分钟</b></span>
        <span className="pool-session-row">交易时段 <b>{tradingSessionLabel(item.tradingSessions)}</b></span>
        <span>创建 <b>{item.createdAt}</b></span>
      </div>
      <div className="row-actions">
        <button type="button" onClick={(event) => { event.stopPropagation(); onEdit(item); }}>修改</button>
        <button type="button" className="danger-button" onClick={(event) => { event.stopPropagation(); onDelete(item.id); }}>删除</button>
      </div>
    </article>
  );
  };

  return (
    <section className="result-panel pool-panel" ref={ref}>
      <div className="pool-head">
        <div>
          <p className="eyebrow">Watch Pool</p>
          <h2>品种检测池子</h2>
        </div>
        <div className="pool-head-actions">
          <AntButton className="compact-button" onClick={onEnableAll} disabled={items.length === 0 || allEnabled}>一键开启检测</AntButton>
          <AntButton className="compact-button muted-button" onClick={onDisableAll} disabled={items.length === 0 || allDisabled}>一键关闭检测</AntButton>
          <AntButton className="compact-button" onClick={onScanNow} disabled={items.length === 0 || enabledCount === 0}>立即检测</AntButton>
          <AntButton className="compact-button" loading={importing} onClick={onImportOpen}>批量导入</AntButton>
          <span className="badge">{enabledCount} 个监控中</span>
          <AntButton className="compact-button" onClick={onNew}>新增品种</AntButton>
        </div>
      </div>
      <div className="pool-groups" aria-label="品种检测池子">
        {groupedItems.map((group) => (
          <details className="pool-group" key={group.key}>
            <summary className="pool-group-head">
              <span className="message-tree-marker" aria-hidden="true" />
              <h3>{group.title}</h3>
              <span>{group.items.length} 个</span>
            </summary>
            <div className="pool-group-body">
              <div className="pool-card-grid">
                {group.items.map(renderPoolCard)}
              </div>
            {group.items.length === 0 && <p className="empty pool-group-empty">暂无{group.title}品种。</p>}
            </div>
          </details>
        ))}
      </div>
      {items.length === 0 && <p className="empty">暂无检测品种，点击“新增品种”创建监控项。</p>}
      {importOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={onImportClose}>
          <section
            className="watch-modal import-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="watch-import-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-head">
              <div>
                <p className="eyebrow">Excel Import</p>
                <h2 id="watch-import-title">批量导入检测池</h2>
              </div>
              <AntButton className="icon-button" onClick={onImportClose} aria-label="关闭批量导入">
                关闭
              </AntButton>
            </div>
            <div className="pool-import-box">
              <div className="pool-import-demo">
                <div className="pool-import-demo-head">
                  <strong>Excel demo</strong>
                  <span>必须按 backend/demo.xlsx 格式导入</span>
                </div>
                <div className="pool-import-table-wrap">
                  <table className="pool-import-table">
                    <thead>
                      <tr>{watchPoolImportDemo[0].map((cell) => <th key={cell}>{cell}</th>)}</tr>
                    </thead>
                    <tbody>
                      {watchPoolImportDemo.slice(1).map((row) => (
                        <tr key={row.join("-")}>{row.map((cell, index) => <td key={`${cell}-${index}`}>{cell}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <button
                type="button"
                className={`pool-import-drop ${dragActive ? "active" : ""}`}
                disabled={importing}
                onClick={() => importInputRef.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragActive(false);
                  const file = event.dataTransfer.files?.[0];
                  if (file) {
                    onImportFile(file);
                  }
                }}
              >
                <strong>{importing ? "导入中..." : "拖入 Excel 文件或点击选择"}</strong>
                <span>仅支持 .xlsx 文件，重复检测池会跳过并报告。</span>
              </button>
              <input
                ref={importInputRef}
                type="file"
                accept=".xlsx"
                className="visually-hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) {
                    onImportFile(file);
                  }
                }}
              />
              {importResult && (
                <div className="pool-import-result">
                  <div className="pool-import-summary">
                    <span><b>{importResult.inserted}</b> 新增</span>
                    <span><b>{importResult.skipped}</b> 跳过</span>
                    <span><b>{importResult.failed}</b> 失败</span>
                  </div>
                  {(importResult.duplicates.length > 0 || importResult.errors.length > 0) && (
                    <div className="pool-import-issues">
                      {[...importResult.duplicates, ...importResult.errors].slice(0, 6).map((issue, index) => (
                        <p key={`${issue.row}-${issue.field ?? "row"}-${index}`}>
                          第 {issue.row} 行{issue.symbol ? ` ${issue.symbol}` : ""}{issue.timeframe ? ` ${issue.timeframe}` : ""}：{issue.reason}
                        </p>
                      ))}
                      {importResult.duplicates.length + importResult.errors.length > 6 && <p>其余问题请修正 Excel 后重新导入。</p>}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="modal-actions">
              <AntButton className="muted-button" onClick={onDownloadTemplate}>下载示例</AntButton>
              <AntButton type="primary" loading={importing} onClick={() => importInputRef.current?.click()}>选择文件导入</AntButton>
            </div>
          </section>
        </div>
      )}
      {editorOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
          <section
            className="watch-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="watch-editor-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-head">
              <div>
                <p className="eyebrow">Watch Pool</p>
                <h2 id="watch-editor-title">{editingId ? "修改检测品种" : "新增检测品种"}</h2>
              </div>
              <AntButton className="icon-button" onClick={onCancel} aria-label="关闭检测品种编辑">
                关闭
              </AntButton>
            </div>
            <div className="pool-editor modal-form">
              <label>
                品种名称
                <Input
                  value={draftName}
                  onChange={(event) => onDraftChange((prev) => ({ ...prev, name: event.target.value }))}
                  placeholder="手动输入代码时必填"
                />
              </label>
              <label className="pool-symbol-combobox">
                监控品种
                <Select
                  showSearch
                  value={draft.symbol}
                  options={contractOptions}
                  optionFilterProp="label"
                  placeholder="输入代码或名称搜索"
                  onChange={(nextSymbol) => {
                    const matched = contractOptions.find((item) => item.value === nextSymbol);
                    onDraftChange((prev) => ({ ...prev, symbol: nextSymbol, name: matched?.name ?? "" }));
                  }}
                  optionLabelProp="value"
                  filterOption={(input, option) => String(option?.searchText ?? "").toLowerCase().includes(input.toLowerCase())}
                />
              </label>
              <label>
                监控周期
                <Select
                  value={draft.timeframe}
                  onChange={(value) => onDraftChange((prev) => ({ ...prev, timeframe: value }))}
                  options={TIMEFRAME_OPTIONS.filter((item) => item.value !== "1d")}
                />
              </label>
              <label>
                检测时长
                <InputNumber
                  min={1}
                  value={draft.monitorMinutes}
                  onChange={(value) => onDraftChange((prev) => ({ ...prev, monitorMinutes: Number(value) || 1 }))}
                />
              </label>
              <label>
                头部到左颈，头部到右颈最小高度
                <InputNumber
                  min={0}
                  step={0.01}
                  value={draft.minHeadToNeckHeight}
                  onChange={(value) => onDraftChange((prev) => ({ ...prev, minHeadToNeckHeight: Number(value) || 0 }))}
                  placeholder="0 表示使用策略默认值"
                />
              </label>
              <label>
                左颈到左肩，右颈到右肩最小价差
                <InputNumber
                  min={0}
                  step={0.01}
                  value={draft.minShoulderToNeckHeight}
                  onChange={(value) => onDraftChange((prev) => ({ ...prev, minShoulderToNeckHeight: Number(value) || 0 }))}
                  placeholder="0 表示使用策略默认值"
                />
              </label>
              <div className="pool-session-field">
                <span>交易时间段</span>
                <div className="pool-session-options">
                  {tradingSessionOptions.map((option) => {
                    const selectedSessions = normalizeTradingSessions(draft.tradingSessions || DEFAULT_TRADING_SESSIONS).split(",");
                    const selected = selectedSessions.includes(option.key);
                    return (
                      <button
                        type="button"
                        className={`pool-session-option ${selected ? "selected" : ""}`}
                        key={option.key}
                        aria-pressed={selected}
                        onClick={() => {
                          const next = new Set(selectedSessions);
                          if (selected) {
                            next.delete(option.key);
                          } else {
                            next.add(option.key);
                          }
                          onDraftChange((prev) => ({ ...prev, tradingSessions: normalizeTradingSessions(Array.from(next).join(",")) }));
                        }}
                      >
                        <Checkbox checked={selected} tabIndex={-1} />
                        <span>
                          <strong>{option.label}</strong>
                          <small>{option.range}</small>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <label className="pool-toggle">
                <Checkbox
                  checked={draft.enabled}
                  onChange={(event) => onDraftChange((prev) => ({ ...prev, enabled: event.target.checked }))}
                />
                监控开关
              </label>
            </div>
            <div className="modal-actions">
              <AntButton className="muted-button" onClick={onCancel}>取消</AntButton>
              <AntButton type="primary" onClick={onSave}>{editingId ? "保存修改" : "新增品种"}</AntButton>
            </div>
          </section>
        </div>
      )}
    </section>
  );
});

function FeedbackTabs({
  activeTab,
  onTabChange,
  monitorAlerts,
  currentSignals,
  feedbacks,
  selectedAlertId,
  selectedSignalKey,
  selectedFeedbackId,
  onSelectAlert,
  onOpenAlertDetail,
  onOpenScoreDetail,
  onHideAlert,
  onFeedbackAlert,
  monitorScrollTargetSymbol,
  onMonitorScrollComplete,
  onFocusCurrentSignal,
  onSelectCurrentSignal,
  onOpenCurrentScoreDetail,
  onSelectFeedback,
  onDeleteFeedback,
}: {
  activeTab: FeedbackTab;
  onTabChange: (tab: FeedbackTab) => void;
  monitorAlerts: HeadShouldersAlertSummary[];
  currentSignals: Signal[];
  feedbacks: AlertFeedback[];
  selectedAlertId: string | null;
  selectedSignalKey: string | null;
  selectedFeedbackId: string | null;
  onSelectAlert: (alert: HeadShouldersAlertSummary) => void;
  onOpenAlertDetail: (alert: HeadShouldersAlertSummary) => void;
  onOpenScoreDetail: (signal: Signal) => void;
  onHideAlert: (alertId: string) => void;
  onFeedbackAlert: (alert: HeadShouldersAlertSummary) => void;
  monitorScrollTargetSymbol: string | null;
  onMonitorScrollComplete: () => void;
  onFocusCurrentSignal: (signal: Signal) => void;
  onSelectCurrentSignal: (signal: Signal) => void;
  onOpenCurrentScoreDetail: (signal: Signal) => void;
  onSelectFeedback: (feedback: AlertFeedback) => void;
  onDeleteFeedback: (id: string) => void;
}) {
  return (
    <section className="message-panel feedback-tabs-panel">
      <div className="feedback-head">
        <div>
          <p className="eyebrow">&#21453;&#39304;</p>
          <h2>&#21491;&#20391;&#21453;&#39304;</h2>
        </div>
        <span className="badge">{monitorAlerts.length} &#26465;&#30417;&#25511;&#28040;&#24687;</span>
      </div>
      <div className="feedback-tabs" role="tablist">
        <button type="button" className={activeTab === "alerts" ? "active" : ""} onClick={() => onTabChange("alerts")}>&#30417;&#25511;&#28040;&#24687;</button>
        <button type="button" className={activeTab === "current" ? "active" : ""} onClick={() => onTabChange("current")}>&#24403;&#21069;&#22270;&#32467;&#26524;</button>
      </div>
      {activeTab === "alerts" && (
        <MonitorAlertFeed
          alerts={monitorAlerts}
          selectedId={selectedAlertId}
          onSelect={onSelectAlert}
          onOpenDetail={onOpenAlertDetail}
          onOpenScoreDetail={onOpenScoreDetail}
          onHide={onHideAlert}
          onFeedback={onFeedbackAlert}
          targetSymbol={monitorScrollTargetSymbol}
          onTargetHandled={onMonitorScrollComplete}
        />
      )}
      {activeTab === "current" && (
        <CurrentSignalFeed
          signals={currentSignals}
          selectedKey={selectedSignalKey}
          onFocus={onFocusCurrentSignal}
          onSelect={onSelectCurrentSignal}
          onOpenScoreDetail={onOpenCurrentScoreDetail}
        />
      )}
      {activeTab === "feedbacks" && (
        <FeedbackFeed
          feedbacks={feedbacks}
          selectedId={selectedFeedbackId}
          onSelect={onSelectFeedback}
          onDelete={onDeleteFeedback}
        />
      )}
    </section>
  );
}

function MonitorAlertFeed({
  alerts,
  selectedId,
  onSelect,
  onOpenDetail,
  onOpenScoreDetail,
  onHide,
  onFeedback,
  targetSymbol,
  onTargetHandled,
}: {
  alerts: HeadShouldersAlertSummary[];
  selectedId: string | null;
  onSelect: (alert: HeadShouldersAlertSummary) => void;
  onOpenDetail: (alert: HeadShouldersAlertSummary) => void;
  onOpenScoreDetail: (signal: Signal) => void;
  onHide: (alertId: string) => void;
  onFeedback: (alert: HeadShouldersAlertSummary) => void;
  targetSymbol: string | null;
  onTargetHandled: () => void;
}) {
  const groupRefs = useRef<Record<string, HTMLDetailsElement | null>>({});
  const groupedAlerts = useMemo(() => {
    const groups = new Map<string, HeadShouldersAlertSummary[]>();
    for (const alert of alerts) {
      const key = alert.symbol || "UNKNOWN";
      const group = groups.get(key);
      if (group) {
        group.push(alert);
      } else {
        groups.set(key, [alert]);
      }
    }
    return Array.from(groups, ([symbol, items]) => ({
      symbol,
      alerts: items,
      confirmedCount: items.filter((alert) => alert.alert_type === "neckline_break").length,
      latestTime: formatMessageTreeLatestTime(items.reduce<string | null>((latest, alert) => {
        if (!alert.created_at) {
          return latest;
        }
        if (!latest) {
          return alert.created_at;
        }
        return alert.created_at > latest ? alert.created_at : latest;
      }, null)),
    }));
  }, [alerts]);

  useEffect(() => {
    if (!targetSymbol) {
      return;
    }
    const target = groupRefs.current[targetSymbol];
    if (!target) {
      return;
    }
    target.open = true;
    window.setTimeout(() => {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      onTargetHandled();
    }, 0);
  }, [targetSymbol, groupedAlerts, onTargetHandled]);

  return (
    <div className="message-list monitor-message-list">
      {alerts.length === 0 ? (
        <p className="empty">&#26242;&#26080;&#30417;&#25511;&#28040;&#24687;&#12290;</p>
      ) : groupedAlerts.map((group) => (
            <details
              className="message-tree-group"
              key={group.symbol}
              ref={(element) => {
                groupRefs.current[group.symbol] = element;
              }}
            >
              <summary className="message-tree-summary">
                <span className="message-tree-marker" aria-hidden="true" />
                <div>
                  <strong>{group.symbol}</strong>
                  <small>{group.alerts.length} &#26465;&#30417;&#25511;&#28040;&#24687;</small>
                </div>
                <span className="message-tree-summary-meta">
                  <span className="message-tree-latest-time">
                    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                      <circle cx="8" cy="8" r="5.5" />
                      <path d="M8 4.8v3.4l2.3 1.4" />
                    </svg>
                    {group.latestTime}
                  </span>
                  {group.confirmedCount > 0 && <b>{group.confirmedCount}</b>}
                </span>
              </summary>
              <div className="message-tree-children">
                {group.alerts.map((alert) => (
                  <article
                    className={`message-item ${alert.alert_type === "neckline_break" ? "confirmed" : ""} ${selectedId === alert.id ? "selected" : ""}`}
                    key={alert.id}
                    onClick={() => onSelect(alert)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(alert);
                      }
                    }}
                  >
                    <button type="button" className="message-close-button monitor-close-button" aria-label="&#20851;&#38381;" onClick={(event) => { event.stopPropagation(); onHide(alert.id); }}>
                      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                        <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
                      </svg>
                    </button>
                    <div className="message-main monitor-message-main">
                      <div className="monitor-alert-tags">
                        <span className={`monitor-tag timeframe-tag timeframe-${alert.timeframe.replace(/[^a-zA-Z0-9]/g, "")}`}>{alert.timeframe}</span>
                        <span className={`monitor-tag pattern-tag ${alert.pattern}`}>{patternLabel(alert.pattern)}</span>
                        <span className={`monitor-tag alert-tag ${alert.alert_type}`}>{alertTypeLabel(alert.alert_type)}</span>
                        <button type="button" className="monitor-tag score-tag score-detail-trigger" onClick={(event) => { event.stopPropagation(); onOpenScoreDetail(alert.signal_payload); }}>{alert.score}</button>
                      </div>
                      <div className="monitor-message-footer">
                        <div className="message-card-actions">
                          <button type="button" className="message-detail-button" onClick={(event) => { event.stopPropagation(); onOpenDetail(alert); }}>&#35814;&#24773;</button>
                          <button type="button" className="message-detail-button" onClick={(event) => { event.stopPropagation(); onFeedback(alert); }}>&#21453;&#39304;</button>
                        </div>
                        <time>{alert.created_at ? formatAlertTime(alert.created_at) : "--"}</time>
                      </div>
                    </div>
                    <button type="button" className="score-badge-button" onClick={(event) => { event.stopPropagation(); onOpenScoreDetail(alert.signal_payload); }}>{alert.score}</button>
                  </article>
                ))}
              </div>
            </details>
      ))}
    </div>
  );
}

function FeedbackFeed({
  feedbacks,
  selectedId,
  onSelect,
  onDelete,
}: {
  feedbacks: AlertFeedback[];
  selectedId: string | null;
  onSelect: (feedback: AlertFeedback) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="message-list">
      {feedbacks.length === 0 ? (
        <p className="empty">&#26242;&#26080;&#21453;&#39304;&#35760;&#24405;&#12290;</p>
      ) : feedbacks.map((feedback) => (
        <article
          className={`message-item ${feedback.alert_type === "neckline_break" ? "confirmed" : ""} ${selectedId === feedback.id ? "selected" : ""}`}
          key={feedback.id}
          onClick={() => onSelect(feedback)}
          role="button"
          tabIndex={0}
        >
          <div className="message-main">
            <strong>{feedback.symbol} / {feedback.timeframe}</strong>
            <span>{patternLabel(feedback.pattern)} &middot; {alertTypeLabel(feedback.alert_type)}</span>
            <small>{feedback.created_at ? formatAlertTime(feedback.created_at) : "--"}</small>
            {feedback.feedback_note && <p className="feedback-note-preview">{feedback.feedback_note}</p>}
          </div>
          <b>{feedback.score}</b>
          <div className="message-card-actions">
            <button type="button" className="message-detail-button" onClick={(event) => { event.stopPropagation(); onSelect(feedback); }}>&#26597;&#30475;</button>
            <button type="button" className="message-detail-button muted-button" onClick={(event) => { event.stopPropagation(); onDelete(feedback.id); }}>&#21024;&#38500;</button>
          </div>
        </article>
      ))}
    </div>
  );
}

function ContractCenterModal({
  contracts,
  refreshState,
  loading,
  updating,
  message,
  onRefresh,
  onUpdate,
  onClose,
}: {
  contracts: ContractCenterItem[];
  refreshState: ContractCenterRefresh | null;
  loading: boolean;
  updating: boolean;
  message: string | null;
  onRefresh: () => void;
  onUpdate: () => void;
  onClose: () => void;
}) {
  const [exchangeFilter, setExchangeFilter] = useState("ALL");
  const visibleContracts = exchangeFilter === "ALL" ? contracts : contracts.filter((item) => item.exchange === exchangeFilter);
  const exchangeCounts = contracts.reduce<Record<string, number>>((acc, item) => {
    acc[item.exchange] = (acc[item.exchange] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="feedback-modal contract-center-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="contract-center-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <AntButton className="modal-close-button" onClick={onClose} aria-label="关闭合约中心">关闭</AntButton>
        <div className="modal-head">
          <div>
            <p className="eyebrow">Contracts</p>
            <h2 id="contract-center-title">合约中心</h2>
          </div>
          <div className="contract-center-actions">
            <AntButton className="compact-button" onClick={onRefresh} disabled={loading || updating} loading={loading}>
              {loading ? "获取中..." : "获取最新合约"}
            </AntButton>
            <AntButton
              className="compact-button"
              onClick={onUpdate}
              disabled={updating || !refreshState || (refreshState.new_symbols.length === 0 && refreshState.stale_symbols.length === 0)}
              loading={updating}
            >
              {updating ? "更新中..." : "确认更新"}
            </AntButton>
          </div>
        </div>
        <div className="contract-summary">
          <span className="badge">已存 {contracts.length} 个</span>
          <span className="badge">SHFE {exchangeCounts.SHFE ?? 0}</span>
          <span className="badge">DCE {exchangeCounts.DCE ?? 0}</span>
          <span className="badge">CZCE {exchangeCounts.CZCE ?? 0}</span>
          {refreshState && <span className="badge">新增 {refreshState.new_count} 个</span>}
          {refreshState && <span className="badge">失效 {refreshState.stale_count} 个</span>}
        </div>
        {message && <div className="contract-message">{message}</div>}
        {refreshState && refreshState.new_symbols.length > 0 && (
          <div className="contract-new-box">
            <strong>待新增合约</strong>
            <div className="contract-chip-list">
              {refreshState.new_symbols.slice(0, 80).map((symbol) => <span key={symbol}>{symbol}</span>)}
              {refreshState.new_symbols.length > 80 && <span>+{refreshState.new_symbols.length - 80}</span>}
            </div>
          </div>
        )}
        {refreshState && refreshState.stale_symbols.length > 0 && (
          <div className="contract-new-box contract-stale-box">
            <strong>待移除失效合约</strong>
            <div className="contract-chip-list">
              {refreshState.stale_symbols.slice(0, 80).map((symbol) => <span key={symbol}>{symbol}</span>)}
              {refreshState.stale_symbols.length > 80 && <span>+{refreshState.stale_symbols.length - 80}</span>}
            </div>
          </div>
        )}
        <div className="contract-filter-row">
          {["ALL", "SHFE", "DCE", "CZCE"].map((exchange) => (
            <AntButton
              key={exchange}
              className={exchangeFilter === exchange ? "active" : ""}
              onClick={() => setExchangeFilter(exchange)}
            >
              {exchange === "ALL" ? "全部" : exchange}
            </AntButton>
          ))}
        </div>
        <div className="contract-table-wrap">
          {visibleContracts.length === 0 ? (
            <p className="empty">暂无合约记录，点击“获取最新合约”从 TqSdk 同步。</p>
          ) : (
            <table className="contract-table">
              <thead>
                <tr>
                  <th>合约代码</th>
                  <th>交易所</th>
                  <th>名称</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                {visibleContracts.map((contract) => (
                  <tr key={contract.id}>
                    <td>{contract.symbol}</td>
                    <td>{contract.exchange}</td>
                    <td>{contract.name}</td>
                    <td>{contract.updated_at ? formatAlertTime(contract.updated_at) : "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}

function CurrentSignalFeed({
  signals,
  selectedKey,
  onFocus,
  onSelect,
  onOpenScoreDetail,
}: {
  signals: Signal[];
  selectedKey: string | null;
  onFocus: (signal: Signal) => void;
  onSelect: (signal: Signal) => void;
  onOpenScoreDetail: (signal: Signal) => void;
}) {
  return (
    <>
      <div className="message-list current-signal-list monitor-message-list">
        {signals.length === 0 ? (
          <p className="empty">当前图暂无头肩顶结果。左侧直接搜索识别到的结果会显示在这里。</p>
        ) : signals.map((signal) => {
          const key = signalKey(signal);
          const displayTime = signal.break_time ?? signal.retest_time ?? signal.right_shoulder.time;
          return (
            <article
              className={`message-item ${signal.confirmed ? "confirmed" : ""} ${selectedKey === key ? "selected" : ""}`}
              key={key}
              onClick={() => onFocus(signal)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onFocus(signal);
                }
              }}
            >
              <div className="message-main monitor-message-main">
                <div className="monitor-alert-tags">
                  <span className={`monitor-tag timeframe-tag timeframe-${signal.timeframe.replace(/[^a-zA-Z0-9]/g, "")}`}>{signal.timeframe}</span>
                  <span className={`monitor-tag pattern-tag ${signal.pattern}`}>{patternLabel(signal.pattern)}</span>
                  <span className={`monitor-tag alert-tag ${signal.alert_type}`}>{alertTypeLabel(signal.alert_type)}</span>
                  <button type="button" className="monitor-tag score-tag score-detail-trigger" onClick={(event) => { event.stopPropagation(); onOpenScoreDetail(signal); }}>{signal.score}</button>
                </div>
                <div className="monitor-message-footer">
                  <div className="message-card-actions">
                    <button
                      type="button"
                      className="message-detail-button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(signal);
                      }}
                    >
                      详情
                    </button>
                  </div>
                  <time>{formatTime(displayTime)}</time>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </>
  );
}

function FeedbackDetail({ source, onOpenScoreDetail }: { source: DetailSource; onOpenScoreDetail: (signal: Signal) => void }) {
  const signal = source?.kind === "alert" ? source.alert.signal_payload : source?.signal ?? null;
  const sourceLabel = source?.kind === "alert" ? "监控消息" : source?.kind === "current" ? "当前图结果" : "";
  return (
    <SignalDetail signal={signal} sourceLabel={sourceLabel} onOpenScoreDetail={onOpenScoreDetail} />
  );
}

function SignalDetail({
  signal,
  sourceLabel = "",
  titleId,
  onOpenScoreDetail,
  onClose,
}: {
  signal: Signal | null;
  sourceLabel?: string;
  titleId?: string;
  onOpenScoreDetail?: (signal: Signal) => void;
  onClose?: () => void;
}) {
  if (!signal) {
    return (
      <section className="detail-panel inline-detail-panel">
        <div className="feedback-head">
          <h2 id={titleId}>详情</h2>
          {onClose && <button type="button" className="icon-button" onClick={onClose}>关闭</button>}
        </div>
        <p className="empty">暂无详情数据，请先选择一条监控消息或当前图结果。</p>
      </section>
    );
  }

  return (
    <section className="detail-panel inline-detail-panel">
      <div className="feedback-head">
        <div>
          <p className="eyebrow">Detail</p>
          <h2 id={titleId}>检测详情</h2>
        </div>
        <div className="detail-head-actions">
          <span className="badge">{sourceLabel || alertTypeLabel(signal.alert_type)}</span>
          {onClose && <button type="button" className="icon-button" onClick={onClose}>关闭</button>}
        </div>
      </div>
      <div className="detail-score">
        <button type="button" className="detail-score-button" onClick={() => onOpenScoreDetail?.(signal)}>{signal.score}</button>
        <p>{signal.trend_label ? `${signal.trend_label} · ` : ""}{translateResultText(signal.message)}</p>
      </div>
      <div className="detail-grid">
        <div><span>左肩</span><strong>{formatPrice(signal.left_shoulder.price)}</strong><small>{formatTime(signal.left_shoulder.time)}</small></div>
        <div><span>左颈</span><strong>{formatPrice(signal.left_neck.price)}</strong><small>{formatTime(signal.left_neck.time)}</small></div>
        <div><span>头部</span><strong>{formatPrice(signal.head.price)}</strong><small>{formatTime(signal.head.time)}</small></div>
        <div><span>右颈</span><strong>{formatPrice(signal.right_neck.price)}</strong><small>{formatTime(signal.right_neck.time)}</small></div>
        <div><span>右肩</span><strong>{formatPrice(signal.right_shoulder.price)}</strong><small>{formatTime(signal.right_shoulder.time)}</small></div>
        <div><span>颈线价</span><strong>{formatPrice(signal.neckline_price)}</strong><small>{signal.confirmed ? "已触发" : "观察中"}</small></div>
      </div>
      <ul className="detail-reasons">
        {signal.reasons.slice(0, 10).map((reason) => <li key={reason}>{translateResultText(reason)}</li>)}
      </ul>
    </section>
  );
}

type ScoreLine = {
  label: string;
  value: string;
  raw: string;
};

type ScoreSection = {
  key: "hourly" | "daily";
  title: string;
  score: string;
  items: ScoreLine[];
};

function ScoreDetailModal({ signal, onClose }: { signal: Signal; onClose: () => void }) {
  const sections = buildScoreSections(signal);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="feedback-modal score-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="score-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button type="button" className="modal-close-button" onClick={onClose} aria-label="关闭评分详情">关闭</button>
        <div className="modal-head score-detail-head">
          <div>
            <p className="eyebrow">Score</p>
            <h2 id="score-detail-title">评分详情</h2>
          </div>
          <div className="score-detail-total">
            <strong>{signal.score}</strong>
            <span>{signal.trend_label || "未分类"}</span>
          </div>
        </div>
        <div className="score-detail-summary">
          <span>{patternLabel(signal.pattern)}</span>
          <span>{alertTypeLabel(signal.alert_type)}</span>
          <span>{signal.timeframe}</span>
        </div>
        <div className="score-section-grid">
          {sections.map((section) => (
            <section className="score-section" key={section.key}>
              <div className="score-section-head">
                <h3>{section.title}</h3>
                <strong>{section.score}</strong>
              </div>
              <div className="score-lines">
                {section.items.length === 0 ? (
                  <p className="score-empty">暂无评分细项</p>
                ) : section.items.map((item) => (
                  <div className="score-line" key={`${section.key}-${item.raw}`}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                    <small>{translateScoreReason(item.raw)}</small>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </section>
    </div>
  );
}

function buildScoreSections(signal: Signal): ScoreSection[] {
  return [
    buildScoreSection(signal.reasons, "hourly", "Hourly", "小时线"),
    buildScoreSection(signal.reasons, "daily", "Daily", "日线"),
  ];
}

function buildScoreSection(
  reasons: string[],
  key: ScoreSection["key"],
  marker: "Hourly" | "Daily",
  title: string,
): ScoreSection {
  const markerTitle = marker === "Hourly" ? "小时线" : "日线";
  const startIndex = reasons.findIndex((reason) => reason.startsWith(`${marker} timeframe`) || reason.startsWith(`${markerTitle}评分`));
  if (startIndex < 0) {
    return { key, title, score: "0/50", items: [] };
  }

  const nextIndex = reasons.findIndex((reason, index) => index > startIndex && (/^(Hourly|Daily) timeframe/.test(reason) || /^(小时线|日线)评分/.test(reason)));
  const endIndex = nextIndex < 0 ? reasons.length : nextIndex;
  const header = reasons[startIndex];
  const detailReasons = reasons.slice(startIndex + 1, endIndex).filter(isScoreDetailReason);
  const items = detailReasons.map(scoreReasonToLine);

  if (items.length === 0 && isScoreDetailReason(header)) {
    items.push(scoreReasonToLine(header));
  }

  return {
    key,
    title,
    score: parseScoreValue(header) ?? "0/50",
    items,
  };
}

function isScoreDetailReason(reason: string) {
  return (
    reason.startsWith("MA arrangement") ||
    reason.startsWith("均线排列目标为") ||
    reason.includes("slope target") ||
    reason.includes("斜率目标为") ||
    /^Close (above|below) \d+\/\d+ tracked MAs/.test(reason) ||
    /^收盘价位于 \d+\/5 条跟踪均线/.test(reason) ||
    reason.startsWith("MA bandwidth") ||
    reason.startsWith("均线带宽") ||
    /^Close (above|below) MA60 confirmation/.test(reason) ||
    /^收盘价(站上|跌破) MA60 确认项/.test(reason) ||
    reason.includes("score unavailable") ||
    reason.includes("评分数据不可用") ||
    reason.includes("data is insufficient") ||
    reason.includes("数据不足") ||
    reason.includes("has no bar at or before signal time") ||
    reason.includes("没有可用K线")
  );
}

function scoreReasonToLine(reason: string): ScoreLine {
  return {
    label: scoreReasonLabel(reason),
    value: parseScoreValue(reason) ?? "-",
    raw: reason,
  };
}

function parseScoreValue(reason: string) {
  return reason.match(/(\d+(?:\.\d+)?\s*\/\s*\d+(?:\.\d+)?)/)?.[1].replace(/\s+/g, "") ?? null;
}

function scoreReasonLabel(reason: string) {
  if (reason.startsWith("MA arrangement")) {
    return "均线排列";
  }
  if (reason.startsWith("均线排列目标为")) {
    return "均线排列";
  }
  if (reason.includes("slope target") || reason.includes("斜率目标为")) {
    return "均线斜率";
  }
  if (/^Close (above|below) \d+\/\d+ tracked MAs/.test(reason) || /^收盘价位于 \d+\/5 条跟踪均线/.test(reason)) {
    return "价格位置";
  }
  if (reason.startsWith("MA bandwidth") || reason.startsWith("均线带宽")) {
    return "均线发散/收敛";
  }
  if (/^Close (above|below) MA60 confirmation/.test(reason) || /^收盘价(站上|跌破) MA60 确认项/.test(reason)) {
    return "MA60确认";
  }
  return "数据状态";
}

function translateScoreReason(reason: string) {
  const arrangement = reason.match(/^MA arrangement target (MA5 [<>] MA10 [<>] MA20 [<>] MA30 [<>] MA60): ([\d.]+\/\d+)/);
  if (arrangement) {
    const direction = arrangement[1].includes(">") ? "多头排列" : "空头排列";
    return `目标为${direction}：${arrangement[1]}，得分 ${arrangement[2]}`;
  }
  const chineseArrangement = reason.match(/^均线排列目标为(多头排列|空头排列) (MA5 [<>] MA10 [<>] MA20 [<>] MA30 [<>] MA60)：([\d.]+\/\d+)/);
  if (chineseArrangement) {
    return `目标为${chineseArrangement[1]}：${chineseArrangement[2]}，得分 ${chineseArrangement[3]}`;
  }

  const slope = reason.match(/^(MA\d+\/MA\d+) slope target (up|down), lookback (\d+): ([\d.]+\/\d+)/);
  if (slope) {
    return `${slope[1]} 斜率目标为${slope[2] === "up" ? "向上" : "向下"}，回看 ${slope[3]} 根K线，得分 ${slope[4]}`;
  }
  const chineseSlope = reason.match(/^(MA\d+\/MA\d+) 斜率目标为(向上|向下)，回看 (\d+) 根K线：([\d.]+\/\d+)/);
  if (chineseSlope) {
    return `${chineseSlope[1]} 斜率目标为${chineseSlope[2]}，回看 ${chineseSlope[3]} 根K线，得分 ${chineseSlope[4]}`;
  }

  const slopeInsufficient = reason.match(/^(MA\d+\/MA\d+) slope data is insufficient: ([\d.]+\/\d+)/);
  if (slopeInsufficient) {
    return `${slopeInsufficient[1]} 斜率数据不足，得分 ${slopeInsufficient[2]}`;
  }

  const priceLocation = reason.match(/^Close (above|below) (\d+)\/5 tracked MAs: ([\d.]+\/\d+)/);
  if (priceLocation) {
    return `收盘价位于 ${priceLocation[2]}/5 条跟踪均线${priceLocation[1] === "above" ? "上方" : "下方"}，得分 ${priceLocation[3]}`;
  }
  const chinesePriceLocation = reason.match(/^收盘价位于 (\d+)\/5 条跟踪均线(上方|下方)：([\d.]+\/\d+)/);
  if (chinesePriceLocation) {
    return `收盘价位于 ${chinesePriceLocation[1]}/5 条跟踪均线${chinesePriceLocation[2]}，得分 ${chinesePriceLocation[3]}`;
  }

  const bandwidth = reason.match(/^MA bandwidth (target trend expanding|target trend narrowing|opposite trend expanding|opposite trend narrowing|mixed trend): ([\d.]+\/\d+)/);
  if (bandwidth) {
    const states: Record<string, string> = {
      "target trend expanding": "目标趋势排列且均线带宽扩大",
      "target trend narrowing": "目标趋势排列但均线带宽收窄",
      "opposite trend expanding": "反向趋势排列且均线带宽扩大",
      "opposite trend narrowing": "反向趋势排列但均线带宽收窄",
      "mixed trend": "均线排列混合，偏震荡",
    };
    return `${states[bandwidth[1]]}，得分 ${bandwidth[2]}`;
  }
  const chineseBandwidth = reason.match(/^均线带宽：(.+)：([\d.]+\/\d+)/);
  if (chineseBandwidth) {
    return `${chineseBandwidth[1]}，得分 ${chineseBandwidth[2]}`;
  }

  const confirmation = reason.match(/^Close (above|below) MA60 confirmation: ([\d.]+\/\d+)/);
  if (confirmation) {
    return `收盘价${confirmation[1] === "above" ? "站上" : "跌破"} MA60 确认项，得分 ${confirmation[2]}`;
  }
  const chineseConfirmation = reason.match(/^收盘价(站上|跌破) MA60 确认项：([\d.]+\/\d+)/);
  if (chineseConfirmation) {
    return `收盘价${chineseConfirmation[1]} MA60 确认项，得分 ${chineseConfirmation[2]}`;
  }
  const chineseTimeframeScore = reason.match(/^(小时线|日线)评分：([\d.]+\/\d+)/);
  if (chineseTimeframeScore) {
    return `${chineseTimeframeScore[1]}评分 ${chineseTimeframeScore[2]}`;
  }

  const timeframeScore = reason.match(/^(Hourly|Daily) timeframe score: ([\d.]+\/\d+)/);
  if (timeframeScore) {
    return `${timeframeScore[1] === "Hourly" ? "小时线" : "日线"}评分 ${timeframeScore[2]}`;
  }

  const unavailable = reason.match(/^(Hourly|Daily) timeframe score unavailable: ([\d.]+\/\d+)/);
  if (unavailable) {
    return `${unavailable[1] === "Hourly" ? "小时线" : "日线"}评分数据不可用，得分 ${unavailable[2]}`;
  }

  const noBar = reason.match(/^(Hourly|Daily) timeframe has no bar at or before signal time: ([\d.]+\/\d+)/);
  if (noBar) {
    return `${noBar[1] === "Hourly" ? "小时线" : "日线"}在信号时间前没有可用K线，得分 ${noBar[2]}`;
  }

  if (reason === "MA slope data is insufficient: 0/10") {
    return "均线斜率数据不足，得分 0/10";
  }
  if (reason === "MA bandwidth data is insufficient: 0/10") {
    return "均线带宽数据不足，得分 0/10";
  }
  if (reason === "MA bandwidth comparison data is insufficient: 0/10") {
    return "均线带宽对比数据不足，得分 0/10";
  }
  if (reason === "MA5/MA10/MA20/MA30/MA60 data is insufficient: 0/50") {
    return "MA5、MA10、MA20、MA30、MA60 数据不足，得分 0/50";
  }

  return translateResultText(reason);
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

    const chart = echarts.init(chartRef.current, undefined, {
      renderer: "canvas",
      devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
      useDirtyRect: true,
    });
    const categories = candles.map((candle) => formatShortTime(candle.time));
    const ohlc = candles.map((candle) => [candle.open, candle.close, candle.low, candle.high]);
    const volumes = candles.map((candle) => ({
      value: candle.volume,
      itemStyle: { color: candle.close >= candle.open ? "rgba(194,65,52,0.42)" : "rgba(22,138,85,0.42)" },
    }));
    const maKeys = Object.keys(candles.find((candle) => candle.ma && Object.keys(candle.ma).length > 0)?.ma ?? {})
      .sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
    const maColors: Record<string, string> = {
      ma5: "#0066cc",
      ma10: "#7a7a7a",
      ma20: "#b87a16",
      ma30: "#168a55",
      ma60: "#c24134",
      ma250: "#333333",
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
        color: signal.confirmed ? (index === 2 ? "#0066cc" : "#2997ff") : (index === 2 ? "#b87a16" : "#7a7a7a"),
        borderColor: "#ffffff",
        borderWidth: 2,
      },
      label: {
        formatter: signal.confirmed ? patternLabels[index] : `疑${patternLabels[index]}`,
        color: "#1d1d1f",
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
            color: signal.confirmed ? "#0066cc" : "#b87a16",
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
    const getChartLayout = () => {
      const height = Math.max(320, chartEl.clientHeight || 0);
      const top = 34;
      const bottom = height < 420 ? 20 : 24;
      const zoomHeight = height < 420 ? 14 : 18;
      const zoomGap = height < 420 ? 8 : 12;
      const volumeHeight = Math.max(44, Math.min(78, Math.round(height * 0.17)));
      const volumeGap = height < 420 ? 16 : 24;
      const available = height - top - bottom - zoomHeight - zoomGap - volumeHeight - volumeGap;
      const priceHeight = Math.max(150, available);
      const volumeTop = top + priceHeight + volumeGap;
      return { bottom, zoomHeight, priceHeight, volumeTop, volumeHeight };
    };
    const applyChartLayout = () => {
      const layout = getChartLayout();
      chart.setOption({
        grid: [
          { left: 14, right: 58, top: 34, height: layout.priceHeight },
          { left: 14, right: 58, top: layout.volumeTop, height: layout.volumeHeight },
        ],
        dataZoom: [
          {},
          { bottom: 10, height: layout.zoomHeight },
        ],
      });
      chart.resize();
    };
    const initialLayout = getChartLayout();

    chart.setOption({
      backgroundColor: "#ffffff",
      animation: false,
      color: maKeys.map((key) => maColors[key] ?? "#94a3b8"),
      legend: {
        top: 8,
        left: 14,
        icon: "roundRect",
        itemWidth: 18,
        itemHeight: 3,
        data: maKeys.map((key) => key.toUpperCase()),
        textStyle: { color: "#7a7a7a", fontSize: 11, fontWeight: 600 },
      },
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
        label: { backgroundColor: "#1d1d1f", color: "#ffffff" },
      },
      tooltip: {
        trigger: "axis",
        triggerOn: useLongPressTooltip ? "none" : "mousemove",
        axisPointer: { type: "cross" },
        borderWidth: 1,
        borderColor: "#e0e0e0",
        backgroundColor: "rgba(255,255,255,0.96)",
        textStyle: { color: "#1d1d1f", fontSize: 12 },
        extraCssText: "box-shadow: 0 18px 44px rgba(0,0,0,.14); border-radius: 11px;",
        formatter: (params: unknown) => formatChartTooltip(params, candles),
      },
      grid: [
        { left: 14, right: 58, top: 34, height: initialLayout.priceHeight },
        { left: 14, right: 58, top: initialLayout.volumeTop, height: initialLayout.volumeHeight },
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
          axisLabel: { color: "#7a7a7a", fontSize: 10, hideOverlap: true },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          position: "right",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: "#7a7a7a", fontSize: 11 },
          splitLine: { lineStyle: { color: "#f0f0f0" } },
        },
        {
          scale: true,
          gridIndex: 1,
          position: "right",
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: "#7a7a7a", fontSize: 10, formatter: (value: number) => formatCompactVolume(value) },
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
          bottom: initialLayout.bottom,
          height: initialLayout.zoomHeight,
          realtime: false,
          brushSelect: false,
          borderColor: "#e0e0e0",
          fillerColor: "rgba(0,102,204,0.12)",
          handleStyle: { color: "#0066cc" },
          textStyle: { color: "#7a7a7a" },
        },
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          data: ohlc,
          itemStyle: {
            color: "rgba(194,65,52,0.72)",
            color0: "rgba(22,138,85,0.72)",
            borderColor: "#c24134",
            borderColor0: "#168a55",
          },
          markPoint: { symbol: "circle", symbolSize: 12, data: markPoints },
          markLine: {
            symbol: "none",
            lineStyle: { color: "#0066cc", width: 2, type: "dashed" },
            label: { color: "#1d1d1f", formatter: "颈线" },
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

    const observer = new ResizeObserver(() => applyChartLayout());
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
    signal.alert_type,
    signal.left_shoulder.index,
    signal.head.index,
    signal.right_shoulder.index,
    signal.break_time ?? "未跌破",
    signal.retest_time ?? "未回测",
  ].join("-");
}

function alertTypeLabel(alertType: Signal["alert_type"]) {
  if (alertType === "right_shoulder_confirmed") {
    return "右肩确认";
  }
  return "跌破颈线";
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
    .map(([key, value]) => `<div><span style="color:#7a7a7a">${key.toUpperCase()}</span> ${formatPrice(Number(value))}</div>`)
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

function formatAlertTime(value: string) {
  const date = parseBackendTimestamp(value);
  if (!date) {
    return formatTime(value);
  }
  return formatDateInShanghai(date);
}

function formatMessageTreeLatestTime(value: string | null) {
  if (!value) {
    return "--";
  }
  const date = parseBackendTimestamp(value) ?? parseLocalTimestamp(value);
  if (!date) {
    return formatTime(value).slice(5, 16);
  }
  const parts = getShanghaiDateParts(date);
  const nowParts = getShanghaiDateParts(new Date());
  const dateOnly = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day));
  const todayOnly = Date.UTC(Number(nowParts.year), Number(nowParts.month) - 1, Number(nowParts.day));
  const dayDiff = Math.round((todayOnly - dateOnly) / 86400000);
  if (dayDiff === 0) {
    return `${parts.hour}:${parts.minute}`;
  }
  if (dayDiff === 1) {
    return "昨天";
  }
  if (parts.year === nowParts.year) {
    return `${parts.month}-${parts.day}`;
  }
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function parseBackendTimestamp(value: string) {
  const trimmed = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(trimmed)) {
    return null;
  }
  const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(trimmed);
  if (!hasTimezone) {
    return null;
  }
  const date = new Date(trimmed);
  return Number.isNaN(date.getTime()) ? null : date;
}

function parseLocalTimestamp(value: string) {
  const normalized = value.trim().replace("T", " ");
  const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!match) {
    return null;
  }
  const [, year, month, day, hour, minute, second = "0"] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second));
  return Number.isNaN(date.getTime()) ? null : date;
}

function getShanghaiDateParts(date: Date) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return {
    year: part("year"),
    month: part("month"),
    day: part("day"),
    hour: part("hour"),
    minute: part("minute"),
  };
}

function formatDateInShanghai(date: Date) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")}`;
}

function formatShortTime(value: string) {
  const normalized = formatTime(value);
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
    min_shoulder_to_head_height_ratio: "肩颈高度/颈头高度下限",
    max_shoulder_diff_pct: "左右肩最大差异",
    max_neck_diff_pct: "颈线低点最大差异",
    min_right_leg_to_left_leg_ratio: "右颈到右肩/左肩到左颈下限",
    max_right_leg_to_left_leg_ratio: "右颈到右肩/左肩到左颈上限",
    min_head_to_right_neck_to_left_neck_to_head_ratio: "头部到右颈/左颈到头部下限",
    max_head_to_right_neck_to_left_neck_to_head_ratio: "头部到右颈/左颈到头部上限",
    min_shoulder_to_neck_height: "肩部到颈部最小价差",
    require_head_beyond_shoulders_and_necks: "头部必须突破肩颈",
    require_shoulders_between_opposite_neck_and_head: "肩部必须在对侧颈头之间",
    neckline_break_pct: "颈线跌破幅度",
    max_bars_after_right_shoulder: "右肩后观察K线数",
    max_signal_age_bars: "仅返回最近N根内信号",
    min_score_to_alert: "最低提醒评分",
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
    .replace("Head is below both shoulders", "头部低于左右肩")
    .replace(/shoulders are close, diff ([\d.]+)%/g, "左右肩高度接近，差异 $1%")
    .replace(/neck lows are close, diff ([\d.]+)%/g, "两个颈线低点接近，差异 $1%")
    .replace("right shoulder is not excessively weak", "右肩没有过度走弱")
    .replace("right shoulder is below head", "右肩低于头部")
    .replace("MACD top divergence: price new high but MACD histogram lower", "出现 MACD 顶背离：头部价格创新高，但 MACD柱 降低")
    .replace("MACD top divergence: price new high but DIF lower", "出现 MACD 顶背离：头部价格创新高，但 DIF 降低")
    .replace(/neckline break confirmed, break ([\d.]+), neckline ([\d.]+)/g, "跌破颈线确认，跌破价 $1，颈线价 $2")
    .replace(/close is below MA(\d+)/g, "收盘价在 MA$1 下方")
    .replace("MA filter passed", "均线过滤通过")
    .replace("Confirmed", "已确认")
    .replace("Suspected", "疑似");
}

createRoot(document.getElementById("root")!).render(<App />);
