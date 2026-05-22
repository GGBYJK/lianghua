from __future__ import annotations

import os
import asyncio
import concurrent.futures
import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import httpx
from dotenv import load_dotenv


class MarketApiError(RuntimeError):
    pass


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env", override=True)
load_dotenv(ROOT_DIR / "backend" / ".env", override=False)
logger = logging.getLogger("app.market_client")

TQ_SYMBOL_MAP = {
    "c0": "KQ.m@DCE.c",
    "C.DCE": "KQ.m@DCE.c",
}


@dataclass
class TqKlineSubscription:
    tq_symbol: str
    duration_seconds: int
    limit: int
    klines: pd.DataFrame
    cache: pd.DataFrame | None = None
    waiters: list["TqKlineWaiter"] | None = None


@dataclass
class TqKlineWaiter:
    future: concurrent.futures.Future[pd.DataFrame]
    limit: int
    deadline: float


def get_market_settings() -> dict[str, str | None]:
    provider = os.getenv("MARKET_DATA_PROVIDER", "aliyun").lower()
    if provider == "tqsdk":
        return {
            "provider": "tqsdk",
            "base_url": "TqSdk",
            "api_key_set": "是" if get_tqsdk_account()[0] and get_tqsdk_account()[1] else "否",
            "market_module": os.getenv("TQ_DEFAULT_EXCHANGE", "DCE"),
        }
    if provider == "aliyun":
        return {
            "provider": "aliyun",
            "base_url": os.getenv("ALIYUN_MARKET_KLINE_URL"),
            "api_key_set": "是" if os.getenv("ALIYUN_MARKET_APPCODE") else "否",
            "market_module": os.getenv("ALIYUN_MARKET_PERIOD_PARAM", "period"),
        }
    if provider == "tushare":
        return {
            "provider": "tushare",
            "base_url": "https://api.tushare.pro",
            "api_key_set": "是" if os.getenv("TUSHARE_TOKEN") else "否",
            "market_module": os.getenv("TUSHARE_EXCHANGE", "DCE"),
        }
    return {
        "provider": "infoway",
        "base_url": os.getenv("INFOWAY_BASE_URL", "https://data.infoway.io"),
        "api_key_set": "是" if os.getenv("INFOWAY_API_KEY") else "否",
        "market_module": os.getenv("INFOWAY_MARKET_MODULE", "common"),
    }


async def fetch_kline_from_market(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    provider = os.getenv("MARKET_DATA_PROVIDER", "aliyun").lower()
    if provider == "tqsdk":
        return await fetch_kline_from_tqsdk_market(symbol=symbol, period=period, limit=limit)
    if provider == "aliyun":
        return await fetch_kline_from_aliyun_market(symbol=symbol, period=period, limit=limit)
    if provider == "tushare":
        return await fetch_kline_from_tushare_market(symbol=symbol, period=period, limit=limit)
    return await fetch_kline_from_infoway_market(symbol=symbol, period=period, limit=limit)


async def fetch_market_symbols(symbol_type: str = "DCE", symbols: str | None = None) -> list[dict[str, str | None]]:
    provider = os.getenv("MARKET_DATA_PROVIDER", "aliyun").lower()
    if provider == "tqsdk":
        return tqsdk_symbol_hints(symbols=symbols)
    if provider == "aliyun":
        return aliyun_symbol_hints(symbols=symbols)
    if provider == "tushare":
        return await fetch_tushare_symbols(exchange=symbol_type, symbols=symbols)
    return await fetch_infoway_symbols(symbol_type=symbol_type, symbols=symbols)


def get_tqsdk_account() -> tuple[str | None, str | None]:
    account = os.getenv("TQ_ACCOUNT") or os.getenv("TQ_USER") or os.getenv("TQ_USERNAME")
    return account, os.getenv("TQ_PASSWORD")


def ensure_tqsdk_configured() -> tuple[str, str]:
    account, password = get_tqsdk_account()
    if not account or not password:
        raise MarketApiError("未配置 TqSdk 账号，请在 .env 中填写 TQ_ACCOUNT 和 TQ_PASSWORD")
    return account, password


async def fetch_kline_from_tqsdk_market(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    return await asyncio.to_thread(_fetch_kline_from_tqsdk_market_sync, symbol, period, limit)


def _fetch_kline_from_tqsdk_market_sync(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    tq_symbol = normalize_tqsdk_symbol(symbol)
    duration_seconds = normalize_tqsdk_period(period)
    return get_tqsdk_service().get_kline(tq_symbol=tq_symbol, duration_seconds=duration_seconds, limit=limit)


class TqSdkMarketService:
    def __init__(self) -> None:
        self._command_queue: queue.Queue[tuple[str, tuple[Any, ...], concurrent.futures.Future[Any]]] = queue.Queue()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._startup_error: BaseException | None = None
        self._listed_contracts_cache: set[str] | None = None
        self._listed_contracts_cached_at = 0.0

    def get_kline(self, tq_symbol: str, duration_seconds: int, limit: int) -> pd.DataFrame:
        self.start()
        future: concurrent.futures.Future[pd.DataFrame] = concurrent.futures.Future()
        self._command_queue.put(("get_kline", (tq_symbol, duration_seconds, limit), future))
        timeout = float(os.getenv("TQ_REQUEST_TIMEOUT", os.getenv("TQ_TIMEOUT", "30")))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise MarketApiError(f"TqSdk 行情缓存等待超时：{tq_symbol}") from exc

    def query_contracts(self, exchanges: list[str] | None = None) -> list[str]:
        self.start()
        future: concurrent.futures.Future[list[str]] = concurrent.futures.Future()
        self._command_queue.put(("query_contracts", (exchanges or ["SHFE", "DCE", "CZCE"],), future))
        timeout = float(os.getenv("TQ_QUERY_TIMEOUT", os.getenv("TQ_REQUEST_TIMEOUT", "30")))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise MarketApiError("TqSdk 合约列表查询超时") from exc

    def start(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._command_queue = queue.Queue()
            self._ready.clear()
            self._stopped.clear()
            self._startup_error = None
            self._thread = threading.Thread(target=self._run, name="tqsdk-market-service", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=float(os.getenv("TQ_START_TIMEOUT", "15"))):
            raise MarketApiError("TqSdk 行情服务启动超时")
        if self._startup_error:
            raise MarketApiError(f"TqSdk 行情服务启动失败：{self._startup_error}") from self._startup_error

    def stop(self) -> None:
        thread = self._thread
        if not thread or not thread.is_alive():
            return
        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._command_queue.put(("stop", (), future))
        thread.join(timeout=float(os.getenv("TQ_STOP_TIMEOUT", "5")))

    def _run(self) -> None:
        api: Any | None = None
        subscriptions: dict[tuple[str, int], TqKlineSubscription] = {}
        try:
            account, password = ensure_tqsdk_configured()
            try:
                from tqsdk import TqApi, TqAuth
            except ImportError as exc:
                raise MarketApiError("未安装 tqsdk，请先执行 python -m pip install -r backend/requirements.txt") from exc
            api = TqApi(auth=TqAuth(account, password))
            self._ready.set()
            update_interval = float(os.getenv("TQ_WAIT_UPDATE_SECONDS", "0.2"))
            while not self._stopped.is_set():
                self._drain_commands(api, subscriptions)
                api.wait_update(deadline=time.time() + update_interval)
                self._refresh_caches(subscriptions)
        except BaseException as exc:
            logger.exception("TqSdk market service stopped unexpectedly")
            self._startup_error = exc
            self._ready.set()
            self._fail_pending_commands(exc)
        finally:
            self._stopped.set()
            if api is not None:
                api.close()

    def _drain_commands(self, api: Any, subscriptions: dict[tuple[str, int], TqKlineSubscription]) -> None:
        while True:
            try:
                command, args, future = self._command_queue.get_nowait()
            except queue.Empty:
                return
            if future.cancelled():
                continue
            try:
                if command == "stop":
                    self._stopped.set()
                    if not future.done():
                        future.set_result(None)
                    return
                if command == "get_kline":
                    tq_symbol, duration_seconds, limit = args
                    self._register_kline_waiter(api, subscriptions, tq_symbol, duration_seconds, limit, future)
                if command == "query_contracts":
                    exchanges = args[0]
                    if not future.done():
                        future.set_result(query_main_and_sub_contracts_from_api(api, exchanges=exchanges))
            except BaseException as exc:
                if not future.done():
                    future.set_exception(exc)

    def _register_kline_waiter(
        self,
        api: Any,
        subscriptions: dict[tuple[str, int], TqKlineSubscription],
        tq_symbol: str,
        duration_seconds: int,
        limit: int,
        future: concurrent.futures.Future[pd.DataFrame],
    ) -> None:
        subscription = self._ensure_subscription(api, subscriptions, tq_symbol, duration_seconds, limit)
        if subscription.cache is not None and len(subscription.cache) >= min(limit, 1):
            future.set_result(subscription.cache.tail(limit).copy().reset_index(drop=True))
            return
        deadline = time.time() + float(os.getenv("TQ_FIRST_DATA_TIMEOUT", os.getenv("TQ_TIMEOUT", "30")))
        if subscription.waiters is None:
            subscription.waiters = []
        subscription.waiters.append(TqKlineWaiter(future=future, limit=limit, deadline=deadline))

    def _ensure_subscription(
        self,
        api: Any,
        subscriptions: dict[tuple[str, int], TqKlineSubscription],
        tq_symbol: str,
        duration_seconds: int,
        limit: int,
    ) -> TqKlineSubscription:
        key = (tq_symbol, duration_seconds)
        subscription = subscriptions.get(key)
        if subscription is None or subscription.limit < limit:
            self._ensure_contract_listed(api, tq_symbol)
            data_length = max(limit, int(os.getenv("TQ_MIN_DATA_LENGTH", "420")))
            previous_waiters = subscription.waiters if subscription and subscription.waiters else []
            subscription = TqKlineSubscription(
                tq_symbol=tq_symbol,
                duration_seconds=duration_seconds,
                limit=data_length,
                klines=api.get_kline_serial(tq_symbol, duration_seconds, data_length=data_length),
                waiters=previous_waiters,
            )
            subscriptions[key] = subscription
            logger.info(
                "TqSdk kline subscribed: symbol=%s duration_seconds=%s data_length=%s",
                tq_symbol,
                duration_seconds,
                data_length,
            )
        return subscription

    def _ensure_contract_listed(self, api: Any, tq_symbol: str) -> None:
        if tq_symbol.startswith("KQ."):
            return
        listed_contracts = self._get_listed_contracts(api)
        if tq_symbol not in listed_contracts:
            raise MarketApiError(f"TqSdk 未找到上市合约：{tq_symbol}，请从合约中心选择有效合约")

    def _get_listed_contracts(self, api: Any) -> set[str]:
        ttl_seconds = float(os.getenv("TQ_CONTRACT_CACHE_SECONDS", "300"))
        now = time.time()
        if self._listed_contracts_cache is not None and now - self._listed_contracts_cached_at < ttl_seconds:
            return self._listed_contracts_cache
        all_listed_contracts = api.query_quotes(expired=False)
        if not isinstance(all_listed_contracts, list):
            raise MarketApiError("TqSdk 合约列表返回结构无法识别")
        self._listed_contracts_cache = {contract for contract in all_listed_contracts if isinstance(contract, str)}
        self._listed_contracts_cached_at = now
        return self._listed_contracts_cache

    def _refresh_caches(self, subscriptions: dict[tuple[str, int], TqKlineSubscription]) -> None:
        for subscription in subscriptions.values():
            self._refresh_subscription_cache(subscription)

    def _refresh_subscription_cache(self, subscription: TqKlineSubscription) -> None:
        try:
            subscription.cache = tqsdk_dataframe_to_kline(
                subscription.klines.tail(subscription.limit),
                tq_symbol=subscription.tq_symbol,
            )
            self._resolve_kline_waiters(subscription)
        except MarketApiError:
            if subscription.cache is None:
                logger.debug("TqSdk kline cache not ready: symbol=%s", subscription.tq_symbol)
        self._expire_kline_waiters(subscription)

    def _resolve_kline_waiters(self, subscription: TqKlineSubscription) -> None:
        if not subscription.waiters or subscription.cache is None:
            return
        pending: list[TqKlineWaiter] = []
        for waiter in subscription.waiters:
            if waiter.future.cancelled() or waiter.future.done():
                continue
            if len(subscription.cache) >= min(waiter.limit, 1):
                waiter.future.set_result(subscription.cache.tail(waiter.limit).copy().reset_index(drop=True))
            else:
                pending.append(waiter)
        subscription.waiters = pending

    def _expire_kline_waiters(self, subscription: TqKlineSubscription) -> None:
        if not subscription.waiters:
            return
        now = time.time()
        pending: list[TqKlineWaiter] = []
        for waiter in subscription.waiters:
            if waiter.future.cancelled() or waiter.future.done():
                continue
            if now >= waiter.deadline:
                waiter.future.set_exception(MarketApiError(f"TqSdk 未在超时时间内返回K线数据：{subscription.tq_symbol}"))
            else:
                pending.append(waiter)
        subscription.waiters = pending

    def _fail_pending_commands(self, exc: BaseException) -> None:
        while True:
            try:
                _, _, future = self._command_queue.get_nowait()
            except queue.Empty:
                return
            if not future.done():
                future.set_exception(exc)


_tqsdk_service: TqSdkMarketService | None = None
_tqsdk_service_lock = threading.Lock()


def get_tqsdk_service() -> TqSdkMarketService:
    global _tqsdk_service
    with _tqsdk_service_lock:
        if _tqsdk_service is None:
            _tqsdk_service = TqSdkMarketService()
        return _tqsdk_service


def shutdown_market_clients() -> None:
    if _tqsdk_service is not None:
        _tqsdk_service.stop()


def fetch_tqsdk_contracts(exchanges: list[str] | None = None) -> list[str]:
    return get_tqsdk_service().query_contracts(exchanges=exchanges)


def query_main_and_sub_contracts_from_api(api: Any, exchanges: list[str]) -> list[str]:
    main_by_variety: dict[str, str] = {}
    candidates_by_variety: dict[str, list[str]] = {}
    all_candidates: list[str] = []
    for exchange in [item.strip().upper() for item in exchanges if item.strip()]:
        try:
            main_contracts = api.query_cont_quotes(exchange_id=exchange)
        except Exception as exc:
            logger.debug("TqSdk main contract query skipped: exchange=%s error=%s", exchange, exc)
            continue
        if not isinstance(main_contracts, list):
            continue
        for main_contract in main_contracts:
            if not is_listed_futures_contract(main_contract):
                continue
            variety = contract_to_variety(main_contract)
            if variety is None:
                continue
            main_by_variety[variety] = main_contract
            variety_contracts = query_variety_futures_contracts(api, variety)
            candidates_by_variety[variety] = variety_contracts
            all_candidates.extend(variety_contracts)

    open_interest_by_contract = fetch_open_interest_by_contract(api, sorted(set(all_candidates)))
    result_contracts: set[str] = set(main_by_variety.values())
    for variety, main_contract in main_by_variety.items():
        candidates = [contract for contract in candidates_by_variety.get(variety, []) if contract != main_contract]
        candidates.sort(key=lambda contract: open_interest_by_contract.get(contract, 0), reverse=True)
        if candidates:
            result_contracts.add(candidates[0])
    return sorted(result_contracts)


def query_variety_futures_contracts(api: Any, variety: str) -> list[str]:
    if "." not in variety:
        return []
    exchange, product_id = variety.split(".", 1)
    try:
        contracts = api.query_quotes(ins_class="FUTURE", exchange_id=exchange, product_id=product_id, expired=False)
    except Exception as exc:
        logger.debug("TqSdk variety contracts query skipped: variety=%s error=%s", variety, exc)
        return []
    if not isinstance(contracts, list):
        return []
    return sorted(contract for contract in contracts if is_listed_futures_contract(contract))


def fetch_open_interest_by_contract(api: Any, contracts: list[str]) -> dict[str, float]:
    if not contracts:
        return {}
    try:
        quotes = api.get_quote_list(contracts)
    except Exception as exc:
        logger.debug("TqSdk quote list query skipped: count=%s error=%s", len(contracts), exc)
        return {}
    deadline = time.time() + float(os.getenv("TQ_SUB_MAIN_TIMEOUT", "8"))
    open_interest_by_contract: dict[str, float] = {}
    while time.time() < deadline:
        api.wait_update(deadline=min(deadline, time.time() + 0.5))
        for contract, quote in zip(contracts, quotes):
            value = getattr(quote, "open_interest", 0)
            try:
                open_interest = float(value)
            except (TypeError, ValueError):
                open_interest = 0
            if open_interest > 0:
                open_interest_by_contract[contract] = open_interest
        if len(open_interest_by_contract) >= len(contracts):
            break
    return open_interest_by_contract


def listed_contracts_to_varieties(contracts: list[str], exchanges: list[str]) -> list[str]:
    selected_exchanges = {exchange.strip().upper() for exchange in exchanges if exchange.strip()}
    varieties: set[str] = set()
    for contract in contracts:
        if not is_listed_futures_contract(contract) or "." not in contract:
            continue
        exchange, code = contract.split(".", 1)
        exchange = exchange.upper()
        if exchange not in selected_exchanges:
            continue
        match = re.match(r"([A-Za-z]+)", code)
        if match:
            varieties.add(f"{exchange}.{match.group(1)}")
    return sorted(varieties)


def contract_to_variety(contract: str) -> str | None:
    if "." not in contract:
        return None
    exchange, code = contract.split(".", 1)
    match = re.match(r"([A-Za-z]+)", code)
    if not match:
        return None
    return f"{exchange.upper()}.{match.group(1)}"


def query_tqsdk_contracts_from_api(api: Any, exchanges: list[str]) -> list[str]:
    all_listed_contracts = api.query_quotes(expired=False)
    if not isinstance(all_listed_contracts, list):
        return []
    futures_contracts = [contract for contract in all_listed_contracts if is_listed_futures_contract(contract)]
    shfe_listed = [contract for contract in futures_contracts if contract.startswith("SHFE.")]
    dce_listed = [contract for contract in futures_contracts if contract.startswith("DCE.")]
    czce_listed = [contract for contract in futures_contracts if contract.startswith("CZCE.")]
    listed_by_exchange = {
        "SHFE": shfe_listed,
        "DCE": dce_listed,
        "CZCE": czce_listed,
    }
    selected_exchanges = [exchange.strip().upper() for exchange in exchanges if exchange.strip()]
    selected_contracts = [
        contract
        for exchange in selected_exchanges
        for contract in listed_by_exchange.get(exchange, [])
    ]
    return sorted(selected_contracts)


def is_listed_futures_contract(contract: Any) -> bool:
    if not isinstance(contract, str):
        return False
    code = contract.split(".", 1)[1] if "." in contract else contract
    if "-C-" in code or "-P-" in code:
        return False
    if "-" in code or "$" in code or "&" in code:
        return False
    if re.search(r"[CP]\d+$", code):
        return False
    return True


def normalize_tqsdk_period(period: str) -> int:
    mapping = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "60m": 3600,
        "1h": 3600,
        "1d": 86400,
        "day": 86400,
    }
    if period not in mapping:
        raise MarketApiError(f"TqSdk 暂不支持的K线周期：{period}")
    return mapping[period]


def normalize_tqsdk_symbol(symbol: str) -> str:
    value = symbol.strip()
    if not value:
        raise MarketApiError("TqSdk 合约代码不能为空")
    mapped = TQ_SYMBOL_MAP.get(value) or TQ_SYMBOL_MAP.get(value.upper()) or TQ_SYMBOL_MAP.get(value.lower())
    if mapped:
        return mapped
    if "." in value or "@" in value:
        return value
    default_exchange = os.getenv("TQ_DEFAULT_EXCHANGE", "DCE").upper()
    return f"{default_exchange}.{value}"


def tqsdk_dataframe_to_kline(df: pd.DataFrame, tq_symbol: str = "") -> pd.DataFrame:
    df = df.copy()
    required_columns = ["datetime", "open", "high", "low", "close", "volume"]
    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise MarketApiError(f"TqSdk K线数据缺少字段：{', '.join(missing)}")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required_columns).sort_values("datetime").reset_index(drop=True)
    if df.empty:
        suffix = f"：{tq_symbol}" if tq_symbol else ""
        raise MarketApiError(f"TqSdk 没有返回可用K线数据{suffix}")
    return df[required_columns]


def tqsdk_symbol_hints(symbols: str | None = None) -> list[dict[str, str | None]]:
    rows = [
        {"symbol": "c0", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"},
        {"symbol": "KQ.m@DCE.c", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"},
    ]
    if not symbols:
        return rows
    wanted = {item.strip().lower() for item in symbols.split(",") if item.strip()}
    return [row for row in rows if (row["symbol"] or "").lower() in wanted]


def ensure_aliyun_configured() -> tuple[str, str]:
    url = os.getenv("ALIYUN_MARKET_KLINE_URL")
    appcode = os.getenv("ALIYUN_MARKET_APPCODE")
    if not url:
        raise MarketApiError("未配置 ALIYUN_MARKET_KLINE_URL，请填写阿里云市场K线接口地址")
    if not appcode:
        raise MarketApiError("未配置 ALIYUN_MARKET_APPCODE，请填写阿里云市场 AppCode")
    return url, appcode


async def fetch_kline_from_aliyun_market(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    return await asyncio.to_thread(_fetch_kline_from_aliyun_market_sync, symbol, period, limit)


def _fetch_kline_from_aliyun_market_sync(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    url, appcode = ensure_aliyun_configured()
    params = {
        os.getenv("ALIYUN_MARKET_SYMBOL_PARAM", "symbol"): symbol,
        os.getenv("ALIYUN_MARKET_PERIOD_PARAM", "period"): normalize_aliyun_period(period),
        os.getenv("ALIYUN_MARKET_LIMIT_PARAM", "limit"): str(limit),
    }
    extra_params = os.getenv("ALIYUN_MARKET_EXTRA_PARAMS")
    if extra_params:
        for pair in extra_params.split("&"):
            if not pair or "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            params[key] = value

    headers = {"Authorization": f"APPCODE {appcode}"}
    timeout = float(os.getenv("ALIYUN_MARKET_TIMEOUT", "10"))
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        logger.exception(
            "Aliyun kline request failed before response: url=%s params=%s headers=%s",
            url,
            params,
            redact_headers(headers),
        )
        raise MarketApiError(f"阿里云行情接口请求失败：{exc}") from exc

    if response.status_code != 200:
        log_aliyun_response_error(response=response, params=params, headers=headers)
        raise MarketApiError(f"阿里云行情接口HTTP错误：{response.status_code}，{response.text}")

    try:
        payload = response.json()
    except ValueError as exc:
        log_aliyun_response_error(response=response, params=params, headers=headers)
        raise MarketApiError(f"阿里云行情接口返回不是JSON：{response.text}") from exc

    if isinstance(payload, dict):
        code = str(payload.get("Code", payload.get("code", "0")))
        if code not in {"0", "200", "Success", "success"} and not any(key in payload for key in ("Obj", "Data", "data", "result")):
            log_aliyun_response_error(response=response, params=params, headers=headers)
            raise MarketApiError(f"阿里云行情接口返回错误：{json.dumps(payload, ensure_ascii=False)}")

    rows = extract_rows(payload, symbol=symbol)
    if not rows:
        log_aliyun_response_error(response=response, params=params, headers=headers)
        raise MarketApiError("阿里云行情接口没有返回K线数据")
    return rows_to_dataframe(rows)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "Authorization" in redacted:
        value = redacted["Authorization"]
        redacted["Authorization"] = value[:16] + "***" if len(value) > 16 else "***"
    return redacted


def log_aliyun_response_error(
    response: httpx.Response,
    params: dict[str, str],
    headers: dict[str, str],
) -> None:
    logger.error(
        "Aliyun kline response error: status=%s request_url=%s params=%s headers=%s response_headers=%s response_body=%s",
        response.status_code,
        response.request.url,
        params,
        redact_headers(headers),
        dict(response.headers),
        response.text,
    )


def aliyun_symbol_hints(symbols: str | None = None) -> list[dict[str, str | None]]:
    rows = [
        {"symbol": "c0", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn continuous"},
        {"symbol": "c2505", "name_cn": "玉米2505", "name_hk": None, "name_en": "Corn 2505"},
        {"symbol": "c2509", "name_cn": "玉米2509", "name_hk": None, "name_en": "Corn 2509"},
    ]
    if not symbols:
        return rows
    wanted = {item.strip().lower() for item in symbols.split(",") if item.strip()}
    return [row for row in rows if (row["symbol"] or "").lower() in wanted]


def ensure_infoway_configured() -> str:
    api_key = os.getenv("INFOWAY_API_KEY")
    if not api_key:
        raise MarketApiError("未配置 INFOWAY_API_KEY，请填写 Infoway 控制台中的 API Key")
    return api_key


def ensure_tushare_configured() -> str:
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise MarketApiError("未配置 TUSHARE_TOKEN，请先在环境变量或 .env 中填写 Tushare token")
    return token


def get_tushare_pro() -> Any:
    token = ensure_tushare_configured()
    try:
        import tushare as ts
    except ImportError as exc:
        raise MarketApiError("未安装 tushare，请先执行 python -m pip install -r backend/requirements.txt") from exc
    ts.set_token(token)
    return ts.pro_api(token)


async def fetch_kline_from_tushare_market(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    return await asyncio.to_thread(_fetch_kline_from_tushare_market_sync, symbol, period, limit)


def _fetch_kline_from_tushare_market_sync(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    pro = get_tushare_pro()
    ts_code = normalize_tushare_symbol(symbol)
    try:
        if period in {"1m", "5m", "15m", "30m", "60m", "1h"}:
            df = fetch_tushare_minute_kline(pro, ts_code, period, limit)
            if df is not None and not df.empty:
                return tushare_dataframe_to_kline(df)
            if os.getenv("TUSHARE_FALLBACK_DAILY", "true").lower() not in {"1", "true", "yes"}:
                raise MarketApiError("Tushare 分钟K线没有返回数据，请检查分钟接口权限或改用 1d")
        df = fetch_tushare_daily_kline(pro, ts_code, limit)
    except Exception as exc:
        if isinstance(exc, MarketApiError):
            raise
        raise MarketApiError(f"Tushare 行情接口调用失败：{exc}") from exc
    if df is None or df.empty:
        raise MarketApiError(f"Tushare 没有返回 {ts_code} 的K线数据，请检查代码或权限")
    return tushare_dataframe_to_kline(df)


def fetch_tushare_daily_kline(pro: Any, ts_code: str, limit: int) -> pd.DataFrame:
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=max(limit * 4, 180))).strftime("%Y%m%d")
    df = pro.fut_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    return df.head(limit) if isinstance(df, pd.DataFrame) else pd.DataFrame()


def fetch_tushare_minute_kline(pro: Any, ts_code: str, period: str, limit: int) -> pd.DataFrame | None:
    freq_map = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "60min", "1h": "60min"}
    freq = freq_map.get(period)
    if freq is None:
        return None
    try:
        df = pro.ft_mins(ts_code=ts_code, freq=freq)
    except Exception:
        return None
    return df.head(limit) if isinstance(df, pd.DataFrame) else pd.DataFrame()


def tushare_dataframe_to_kline(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "trade_date" in df.columns:
        df["datetime"] = pd.to_datetime(df["trade_date"], errors="coerce")
    elif "trade_time" in df.columns:
        df["datetime"] = pd.to_datetime(df["trade_time"], errors="coerce")
    elif "datetime" not in df.columns and "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], errors="coerce")
    if "vol" in df.columns and "volume" not in df.columns:
        df["volume"] = df["vol"]
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise MarketApiError(f"Tushare 返回数据缺少字段：{col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
    if df.empty:
        raise MarketApiError("Tushare K线数据转换后为空")
    return df.sort_values("datetime").reset_index(drop=True)[["datetime", "open", "high", "low", "close", "volume"]]


async def fetch_tushare_symbols(exchange: str = "DCE", symbols: str | None = None) -> list[dict[str, str | None]]:
    return await asyncio.to_thread(_fetch_tushare_symbols_sync, exchange, symbols)


def _fetch_tushare_symbols_sync(exchange: str = "DCE", symbols: str | None = None) -> list[dict[str, str | None]]:
    pro = get_tushare_pro()
    exchange_code = normalize_tushare_exchange(exchange)
    try:
        df = pro.fut_basic(exchange=exchange_code, fields="ts_code,symbol,name,exchange,list_date,delist_date")
    except Exception as exc:
        raise MarketApiError(f"Tushare 产品列表接口调用失败：{exc}") from exc
    if not isinstance(df, pd.DataFrame):
        return []
    rows = [normalize_tushare_symbol_row(row) for _, row in df.iterrows()]
    rows = add_tushare_main_contracts(rows, exchange_code)
    if symbols:
        wanted = {item.strip().upper() for item in symbols.split(",") if item.strip()}
        rows = [row for row in rows if (row["symbol"] or "").upper() in wanted]
    return rows


def normalize_tushare_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        return value
    if value == "C":
        return "C.DCE"
    return f"{value}.{os.getenv('TUSHARE_EXCHANGE', 'DCE').upper()}"


def normalize_tushare_exchange(exchange: str) -> str:
    value = exchange.upper()
    if value in {"FUTURES", "期货"}:
        return os.getenv("TUSHARE_EXCHANGE", "DCE").upper()
    return value


def normalize_tushare_symbol_row(row: Any) -> dict[str, str | None]:
    name = optional_str(row.get("name"))
    return {
        "symbol": str(row.get("ts_code") or ""),
        "name_cn": name,
        "name_hk": None,
        "name_en": optional_str(row.get("symbol")),
    }


def add_tushare_main_contracts(rows: list[dict[str, str | None]], exchange: str) -> list[dict[str, str | None]]:
    if exchange != "DCE":
        return rows
    main_contracts = [
        {"symbol": "C.DCE", "name_cn": "玉米主力连续", "name_hk": None, "name_en": "Corn Continuous"},
    ]
    existing = {row["symbol"] for row in rows}
    return [item for item in main_contracts if item["symbol"] not in existing] + rows


async def fetch_kline_from_infoway_market(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    return await asyncio.to_thread(_fetch_kline_from_infoway_market_sync, symbol, period, limit)


def _fetch_kline_from_infoway_market_sync(symbol: str, period: str, limit: int = 120) -> pd.DataFrame:
    api_key = ensure_infoway_configured()
    try:
        from infoway import InfowayClient
        from infoway.exceptions import InfowayAPIError, InfowayAuthError, InfowayTimeoutError
    except ImportError as exc:
        raise MarketApiError("未安装 infoway-sdk，请先执行 python -m pip install -r backend/requirements.txt") from exc

    module_name = os.getenv("INFOWAY_MARKET_MODULE", "common").lower()
    base_url = os.getenv("INFOWAY_BASE_URL", "https://data.infoway.io")
    timeout = float(os.getenv("INFOWAY_TIMEOUT", "15"))
    max_retries = int(os.getenv("INFOWAY_MAX_RETRIES", "3"))

    try:
        with InfowayClient(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries) as client:
            market_client = getattr(client, module_name, None)
            if market_client is None or not hasattr(market_client, "get_kline"):
                raise MarketApiError(f"Infoway 不支持的行情模块：{module_name}")
            payload = market_client.get_kline(symbol, kline_type=normalize_period(period), count=limit)
    except InfowayAuthError as exc:
        raise MarketApiError("Infoway API Key 无效或无权限") from exc
    except InfowayTimeoutError as exc:
        raise MarketApiError(f"Infoway 行情接口请求超时：{exc}") from exc
    except InfowayAPIError as exc:
        raise MarketApiError(f"Infoway 行情接口返回错误：{exc}") from exc

    rows = extract_rows(payload, symbol=symbol)
    if not rows:
        raise MarketApiError("Infoway 行情接口没有返回K线数据")

    return rows_to_dataframe(rows)


async def fetch_infoway_symbols(symbol_type: str = "FUTURES", symbols: str | None = None) -> list[dict[str, str | None]]:
    return await asyncio.to_thread(_fetch_infoway_symbols_sync, symbol_type, symbols)


def _fetch_infoway_symbols_sync(symbol_type: str = "FUTURES", symbols: str | None = None) -> list[dict[str, str | None]]:
    api_key = ensure_infoway_configured()
    try:
        from infoway import InfowayClient
        from infoway.exceptions import InfowayAPIError, InfowayAuthError, InfowayTimeoutError
    except ImportError as exc:
        raise MarketApiError("未安装 infoway-sdk，请先执行 python -m pip install -r backend/requirements.txt") from exc

    base_url = os.getenv("INFOWAY_BASE_URL", "https://data.infoway.io")
    timeout = float(os.getenv("INFOWAY_TIMEOUT", "15"))
    max_retries = int(os.getenv("INFOWAY_MAX_RETRIES", "3"))
    params = {"type": symbol_type.upper()}
    if symbols:
        params["symbols"] = symbols

    try:
        with InfowayClient(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries) as client:
            payload = client._http.get("/common/basic/symbols", params=params)
    except InfowayAuthError as exc:
        raise MarketApiError("Infoway API Key 无效或无权限") from exc
    except InfowayTimeoutError as exc:
        raise MarketApiError(f"Infoway 产品列表接口请求超时：{exc}") from exc
    except InfowayAPIError as exc:
        raise MarketApiError(f"Infoway 产品列表接口返回错误：{exc}") from exc

    if not isinstance(payload, list):
        raise MarketApiError("Infoway 产品列表接口返回结构无法识别")
    return [normalize_symbol_row(row) for row in payload if isinstance(row, dict)]


def normalize_symbol_row(row: dict[str, Any]) -> dict[str, str | None]:
    return {
        "symbol": str(row.get("symbol") or ""),
        "name_cn": optional_str(row.get("name_cn")),
        "name_hk": optional_str(row.get("name_hk")),
        "name_en": optional_str(row.get("name_en")),
    }


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def extract_rows(payload: Any, symbol: str | None = None) -> list[Any]:
    if isinstance(payload, list):
        nested = flatten_infoway_kline_rows(payload, symbol=symbol)
        if nested:
            return nested
        return payload
    if not isinstance(payload, dict):
        return []
    nested = flatten_infoway_kline_rows(payload.get("data") or payload.get("Data"), symbol=symbol)
    if nested:
        return nested
    obj = payload.get("Obj") or payload.get("Data") or payload.get("data") or payload.get("result")
    if isinstance(obj, list):
        nested = flatten_infoway_kline_rows(obj, symbol=symbol)
        if nested:
            return nested
        return obj
    if isinstance(obj, dict):
        nested = flatten_infoway_kline_rows(obj, symbol=symbol)
        if nested:
            return nested
        for key in ("List", "list", "Rows", "rows", "KLines", "klines", "Data", "data", "respList"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
    return []


def flatten_infoway_kline_rows(payload: Any, symbol: str | None = None) -> list[Any]:
    if isinstance(payload, dict):
        resp_list = payload.get("respList") or payload.get("resplist")
        if isinstance(resp_list, list):
            return resp_list
        return []
    if not isinstance(payload, list):
        return []
    rows: list[Any] = []
    target_symbol = symbol.lower() if symbol else None
    for item in payload:
        if not isinstance(item, dict):
            continue
        item_symbol = str(item.get("s") or item.get("symbol") or "").lower()
        if target_symbol and item_symbol and item_symbol != target_symbol:
            continue
        resp_list = item.get("respList") or item.get("resplist")
        if isinstance(resp_list, list):
            rows.extend(resp_list)
    return rows


def rows_to_dataframe(rows: list[Any]) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = normalize_row(row)
        if item:
            normalized.append(item)
    if not normalized:
        raise MarketApiError("行情接口K线字段无法识别，请检查返回字段映射")
    df = pd.DataFrame(normalized)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().sort_values("datetime").reset_index(drop=True)
    if df.empty:
        raise MarketApiError("行情接口K线数据转换后为空")
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def normalize_period(period: str) -> int | str:
    mapping = {
        "1m": 1,
        "5m": 2,
        "15m": 3,
        "30m": 4,
        "60m": 5,
        "1h": 5,
        "2h": 6,
        "4h": 7,
        "1d": 8,
        "day": 8,
        "1w": 9,
        "week": 9,
        "1mo": 10,
        "month": 10,
        "quarter": 11,
        "year": 12,
    }
    return mapping.get(period, period)


def normalize_aliyun_period(period: str) -> str:
    mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "60m": "60",
        "1h": "60",
        "1d": "day",
        "day": "day",
    }
    return mapping.get(period, period)


def normalize_row(row: Any) -> dict[str, Any] | None:
    if isinstance(row, dict):
        return normalize_dict_row(row)
    if isinstance(row, (list, tuple)) and len(row) >= 6:
        return {
            "datetime": normalize_time(row[0]),
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
        }
    return None


def normalize_dict_row(row: dict[str, Any]) -> dict[str, Any] | None:
    lowered = {str(k).lower(): v for k, v in row.items()}
    time_value = first_value(lowered, ["datetime", "time", "trade_time", "trade_date", "tick", "t", "date"])
    open_value = first_value(lowered, ["open", "o"])
    high_value = first_value(lowered, ["high", "h"])
    low_value = first_value(lowered, ["low", "l"])
    close_value = first_value(lowered, ["close", "c", "last", "last_price"])
    volume_value = first_value(lowered, ["volume", "vol", "v"])
    if None in [time_value, open_value, high_value, low_value, close_value, volume_value]:
        return None
    return {
        "datetime": normalize_time(time_value),
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }


def first_value(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def normalize_time(value: Any) -> Any:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000)
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number)
    return value
