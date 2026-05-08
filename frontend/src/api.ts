import type { MarketSettings, MarketSymbolsResponse, ScanResponse, SimulationStartResponse, SimulationStepResponse } from "./types";

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

export async function scanMarket(symbol: string, timeframe: string, limit: number): Promise<ScanResponse> {
  const url = new URL("/api/market/scan", API_BASE);
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("timeframe", timeframe);
  url.searchParams.set("limit", String(limit));
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

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail ?? response.statusText;
  } catch {
    return response.statusText;
  }
}
