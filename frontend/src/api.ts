import type { AlertFeedback, ContractCenterItem, ContractCenterRefresh, HeadShouldersAlert, HeadShouldersAlertSummary, MarketSettings, MarketSymbolsResponse, ScanResponse, SimulationStartResponse, SimulationStepResponse, WatchPoolItem, WatchPoolPayload } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || window.location.origin;

export async function getDefaultConfig(symbol: string, timeframe: string): Promise<Record<string, unknown>> {
  const url = new URL("/api/config/default", API_BASE);
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("timeframe", timeframe);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const body = await response.json();
  return body.config;
}

export async function scanCsv(params: {
  file: File;
  symbol: string;
  timeframe: string;
  overrides: Record<string, unknown>;
}): Promise<ScanResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("symbol", params.symbol);
  form.append("timeframe", params.timeframe);
  form.append("config_overrides", JSON.stringify(params.overrides));

  const response = await fetch(`${API_BASE}/api/scan`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function startSimulation(params: {
  file: File;
  symbol: string;
  timeframe: string;
  overrides: Record<string, unknown>;
}): Promise<SimulationStartResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("symbol", params.symbol);
  form.append("timeframe", params.timeframe);
  form.append("config_overrides", JSON.stringify(params.overrides));

  const response = await fetch(`${API_BASE}/api/simulations/start`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function nextSimulationBar(sessionId: string, bars = 1): Promise<SimulationStepResponse> {
  const url = new URL(`/api/simulations/${sessionId}/next`, API_BASE);
  url.searchParams.set("bars", String(bars));
  const response = await fetch(url, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function resetSimulation(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/simulations/${sessionId}/reset`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
}

export async function getMarketSettings(): Promise<MarketSettings> {
  const response = await fetch(`${API_BASE}/api/market/settings`);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function getMarketSymbols(symbolType: string, symbols?: string): Promise<MarketSymbolsResponse> {
  const url = new URL("/api/market/symbols", API_BASE);
  url.searchParams.set("symbol_type", symbolType);
  if (symbols?.trim()) {
    url.searchParams.set("symbols", symbols.trim());
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function scanMarket(
  symbol: string,
  timeframe: string,
  limit: number,
  overrides?: Record<string, unknown>,
): Promise<ScanResponse> {
  const url = new URL("/api/market/scan", API_BASE);
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("timeframe", timeframe);
  url.searchParams.set("limit", String(limit));
  if (overrides && Object.keys(overrides).length > 0) {
    url.searchParams.set("config_overrides", JSON.stringify(overrides));
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function scanSample(symbol: string, timeframe: string): Promise<ScanResponse> {
  const url = new URL("/api/sample/scan", API_BASE);
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("timeframe", timeframe);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function listWatchPool(): Promise<WatchPoolItem[]> {
  const response = await fetch(`${API_BASE}/api/watch-pool`);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function createWatchPoolItem(payload: WatchPoolPayload): Promise<WatchPoolItem> {
  const response = await fetch(`${API_BASE}/api/watch-pool`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function enableAllWatchPoolItems(): Promise<WatchPoolItem[]> {
  const response = await fetch(`${API_BASE}/api/watch-pool/enable-all`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function disableAllWatchPoolItems(): Promise<WatchPoolItem[]> {
  const response = await fetch(`${API_BASE}/api/watch-pool/disable-all`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function updateWatchPoolItem(id: string, payload: WatchPoolPayload): Promise<WatchPoolItem> {
  const response = await fetch(`${API_BASE}/api/watch-pool/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function deleteWatchPoolItem(id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/watch-pool/${id}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
}

export async function listHeadShouldersAlerts(limit = 100): Promise<HeadShouldersAlertSummary[]> {
  const url = new URL("/api/alerts", API_BASE);
  url.searchParams.set("limit", String(limit));
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function getHeadShouldersAlert(id: string): Promise<HeadShouldersAlert> {
  const response = await fetch(`${API_BASE}/api/alerts/${id}`);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function hideHeadShouldersAlert(id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/alerts/${id}/hide`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
}

export async function listAlertFeedbacks(limit = 100): Promise<AlertFeedback[]> {
  const url = new URL("/api/feedbacks", API_BASE);
  url.searchParams.set("limit", String(limit));
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function createAlertFeedback(alertId: string, note: string): Promise<AlertFeedback> {
  const response = await fetch(`${API_BASE}/api/feedbacks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alert_id: alertId, note }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function deleteAlertFeedback(id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/feedbacks/${id}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
}

export async function listContracts(exchange?: string): Promise<ContractCenterItem[]> {
  const url = new URL("/api/contracts", API_BASE);
  if (exchange?.trim()) {
    url.searchParams.set("exchange", exchange.trim());
  }
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function refreshContracts(exchanges = "SHFE,DCE,CZCE"): Promise<ContractCenterRefresh> {
  const url = new URL("/api/contracts/refresh", API_BASE);
  url.searchParams.set("exchanges", exchanges);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function updateContracts(params: {
  symbols: string[];
  latest_symbols?: string[];
  exchanges?: string[];
  prune_missing?: boolean;
}): Promise<{ inserted: number; removed: number; items: ContractCenterItem[] }> {
  const response = await fetch(`${API_BASE}/api/contracts/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function scanWatchPoolOnce(limit = 420): Promise<{ inserted: number }> {
  const url = new URL("/api/alerts/scan-once", API_BASE);
  url.searchParams.set("limit", String(limit));
  const response = await fetch(url, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? response.statusText;
  } catch {
    return response.statusText;
  }
}
