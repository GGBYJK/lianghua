import type {
  AccountSummary,
  ContractSpec,
  LedgerEntry,
  LoginResponse,
  MarketQuote,
  PaperOrder,
  PlatformUser,
  PositionLot,
  ProductCatalogItem,
  ProductCostImportResult,
  ProductDetails,
  TradeSignal,
  BacktestOrdersResponse,
  BacktestRequest,
  BacktestRun,
  BacktestSeries,
  BacktestSymbolGroup,
  BacktestSymbolGroupPayload,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || window.location.origin;
const TOKEN_KEY = "paper-trading-access-token";
let accessToken = window.localStorage.getItem(TOKEN_KEY) || "";
let refreshPromise: Promise<boolean> | null = null;

export function setAccessToken(token: string) {
  accessToken = token;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export function hasAccessToken() {
  return Boolean(accessToken);
}

async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/api/auth/refresh`, {
      method: "POST",
      credentials: "include",
    }).then(async (response) => {
      if (!response.ok) {
        setAccessToken("");
        return false;
      }
      const body = await response.json() as LoginResponse;
      setAccessToken(body.access_token);
      return true;
    }).finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
  if (response.status === 401 && retry && await refreshSession()) {
    return request<T>(path, options, false);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Keep the HTTP status text when the server does not return JSON.
    }
    throw new Error(detail);
  }
  return response.json();
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const result = await request<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  }, false);
  setAccessToken(result.access_token);
  return result;
}

export async function restoreSession(): Promise<PlatformUser | null> {
  if (!hasAccessToken() && !await refreshSession()) return null;
  try {
    return await request<PlatformUser>("/api/auth/me");
  } catch {
    setAccessToken("");
    return null;
  }
}

export async function logout() {
  await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }, false).catch(() => undefined);
  setAccessToken("");
}

export const platformApi = {
  account: () => request<AccountSummary>("/api/trading/account"),
  signals: () => request<TradeSignal[]>("/api/trading/signals?limit=300"),
  positions: () => request<PositionLot[]>("/api/trading/positions"),
  orders: () => request<PaperOrder[]>("/api/trading/orders?limit=200"),
  ledger: () => request<LedgerEntry[]>("/api/trading/ledger?limit=200"),
  contracts: () => request<ContractSpec[]>("/api/trading/contracts"),
  products: () => request<ProductCatalogItem[]>("/api/trading/products"),
  productDetails: (symbol: string) => request<ProductDetails>(`/api/trading/products/details?symbol=${encodeURIComponent(symbol)}`),
  importProductCosts: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<ProductCostImportResult>("/api/admin/product-costs/import", { method: "POST", body });
  },
  quotes: (symbols: string[]) => request<MarketQuote[]>(`/api/trading/quotes?symbols=${encodeURIComponent(symbols.join(","))}`),
  users: () => request<PlatformUser[]>("/api/admin/users"),
  openSignal: (signalId: string, payload: Record<string, unknown>) => request<PaperOrder>(`/api/trading/signals/${signalId}/open`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  openManual: (payload: Record<string, unknown>) => request<PaperOrder>("/api/trading/orders/open", {
    method: "POST", body: JSON.stringify(payload),
  }),
  closePosition: (lotId: string, quantity: number) => request<PaperOrder>(`/api/trading/positions/${lotId}/close`, {
    method: "POST", body: JSON.stringify({ quantity, idempotency_key: crypto.randomUUID() }),
  }),
  updateExitRules: (lotId: string, payload: Record<string, unknown>) => request<{ ok: boolean }>(`/api/trading/positions/${lotId}/exit-rules`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  createUser: (payload: Record<string, unknown>) => request<PlatformUser>("/api/admin/users", {
    method: "POST", body: JSON.stringify(payload),
  }),
  updateUser: (userId: number, payload: Record<string, unknown>) => request<PlatformUser>(`/api/admin/users/${userId}`, {
    method: "PATCH", body: JSON.stringify(payload),
  }),
  adjustAccount: (userId: number, payload: Record<string, unknown>) => request<AccountSummary>(`/api/admin/users/${userId}/account-adjustment`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  saveContract: (symbol: string, payload: Record<string, unknown>) => request<ContractSpec>(`/api/admin/contracts/${encodeURIComponent(symbol)}`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  backtests: () => request<BacktestRun[]>("/api/backtests?limit=100"),
  backtestSymbolGroups: () => request<BacktestSymbolGroup[]>("/api/backtests/symbol-groups"),
  createBacktestSymbolGroup: (payload: BacktestSymbolGroupPayload) => request<BacktestSymbolGroup>("/api/backtests/symbol-groups", {
    method: "POST", body: JSON.stringify(payload),
  }),
  updateBacktestSymbolGroup: (groupId: string, payload: BacktestSymbolGroupPayload) => request<BacktestSymbolGroup>(`/api/backtests/symbol-groups/${encodeURIComponent(groupId)}`, {
    method: "PUT", body: JSON.stringify(payload),
  }),
  deleteBacktestSymbolGroup: (groupId: string) => request<{ ok: boolean }>(`/api/backtests/symbol-groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  }),
  backtest: (runId: string) => request<BacktestRun>(`/api/backtests/${runId}`),
  createBacktest: (payload: BacktestRequest) => request<BacktestRun>("/api/backtests", {
    method: "POST", body: JSON.stringify(payload),
  }),
  backtestOrders: (runId: string, params: URLSearchParams) => request<BacktestOrdersResponse>(`/api/backtests/${runId}/orders?${params.toString()}`),
  backtestSeries: (runId: string, symbol: string, timeframe: string) => request<BacktestSeries>(`/api/backtests/${runId}/series?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  cancelBacktest: (runId: string) => request<{ ok: boolean }>(`/api/backtests/${runId}/cancel`, { method: "POST" }),
  deleteBacktest: (runId: string) => request<{ ok: boolean }>(`/api/backtests/${runId}`, { method: "DELETE" }),
};

export async function downloadBacktest(runId: string) {
  let response = await fetch(`${API_BASE}/api/backtests/${runId}/export`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    credentials: "include",
  });
  if (response.status === 401 && await refreshSession()) {
    response = await fetch(`${API_BASE}/api/backtests/${runId}/export`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      credentials: "include",
    });
  }
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `backtest-${runId}.xlsx`;
  link.click();
  URL.revokeObjectURL(url);
}
