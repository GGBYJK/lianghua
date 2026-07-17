const TIMEZONE_SUFFIX = /(?:z|[+-]\d{2}:?\d{2})$/i;
const API_TIMESTAMP = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

export function parseApiTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!API_TIMESTAMP.test(trimmed)) return null;
  const normalized = TIMEZONE_SUFFIX.test(trimmed) ? trimmed : `${trimmed.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function apiTimestampToMs(value: string | null | undefined): number {
  return parseApiTimestamp(value)?.getTime() ?? 0;
}

function formatInShanghai(date: Date): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")}`;
}

export function formatApiDateTime(value: string | null | undefined, fallback = "--"): string {
  const date = parseApiTimestamp(value);
  return date ? formatInShanghai(date) : fallback;
}

export function formatMarketDateTime(value: string | null | undefined, fallback = "--"): string {
  if (!value) return fallback;
  const trimmed = value.trim();
  if (TIMEZONE_SUFFIX.test(trimmed)) {
    const date = new Date(trimmed);
    return Number.isNaN(date.getTime()) ? fallback : formatInShanghai(date);
  }
  return API_TIMESTAMP.test(trimmed) ? trimmed.replace("T", " ").slice(0, 19) : fallback;
}
