from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

import pandas as pd


MAX_HEAD_NECK_BARS_BY_TIMEFRAME = {
    "1m": 60,
    "3m": 60,
    "5m": 60,
}
MIXED_PIVOT_CONFIRMATION_TIMEFRAMES = {"1m", "3m", "5m"}
INVERSE_PRIOR_HIGH_TIMEFRAMES = {"3m", "5m"}
STRUCTURE_PIVOT_WINDOW = 5
RIGHT_SHOULDER_PIVOT_WINDOW = 3
PULLBACK_LOOKAHEAD_BARS = 60
PULLBACK_MAX_TREND_SCORE = 35
PULLBACK_MIN_PATTERN_SCORE = 80


@dataclass
class HeadShoulderTopConfig:
    pivot_left: int = 3 
    pivot_right: int = 3
    min_shoulder_to_head_height_ratio: float = 0.3
    max_shoulder_diff_pct: float = 0.004
    max_neck_diff_pct: float = 0.004
    min_right_leg_to_left_leg_ratio: float = 0.6
    max_right_leg_to_left_leg_ratio: float = 2.0
    enable_right_leg_ratio_filter: bool = True
    min_right_shoulder_ratio_to_left: float = 0.85
    min_head_to_neck_height: float = 0.0
    require_head_beyond_shoulders_and_necks: bool = False
    require_shoulders_between_opposite_neck_and_head: bool = False
    min_shoulder_to_neck_height: float = 0.0
    right_shoulder_must_below_head: bool = True
    enable_ma_filter: bool = False
    ma_short: int = 3
    ma_mid: int = 5
    ma_long: int = 8
    ma_periods: list[int] = field(default_factory=lambda: [5, 10, 20, 30, 60, 250])
    require_ma_bearish_alignment: bool = True
    require_close_below_ma_long: bool = True
    enable_macd_divergence: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_price_new_high_pct: float = 0.01
    use_macd_hist_for_divergence: bool = True
    break_by: str = "close"
    neckline_break_pct: float = 0.002
    max_bars_after_right_shoulder: int = 30
    max_signal_age_bars: int = 0
    enable_score: bool = True
    min_score_to_alert: int = 70
    min_pattern_score_to_alert: int = 60
    enable_key_zone_trend_score: bool = False
    resistance_zone_min: float = 0.0
    resistance_zone_max: float = 0.0
    support_zone_min: float = 0.0
    support_zone_max: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PivotPoint:
    index: int
    time: pd.Timestamp
    price: float
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "time": self.time.isoformat(),
            "price": self.price,
            "kind": self.kind,
        }


@dataclass
class HeadShoulderTopSignal:
    symbol: str
    timeframe: str
    pattern: str
    left_shoulder: PivotPoint
    left_neck: PivotPoint
    head: PivotPoint
    right_neck: PivotPoint
    right_shoulder: PivotPoint
    neckline_price: float
    confirmed: bool
    score: int
    reasons: list[str]
    qtr: float | None = None
    trend_label: str = ""
    break_time: pd.Timestamp | None = None
    break_price: float | None = None
    retest_time: pd.Timestamp | None = None
    retest_price: float | None = None
    alert_type: str = "right_shoulder_confirmed"
    message: str = ""
    pattern_score: int | None = None
    pattern_raw_score: int | None = None
    pattern_grade: str = ""
    pattern_caps: list[int] = field(default_factory=list)
    pattern_sections: list[dict[str, Any]] = field(default_factory=list)
    pattern_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "pattern": self.pattern,
            "alert_type": self.alert_type,
            "left_shoulder": self.left_shoulder.to_dict(),
            "left_neck": self.left_neck.to_dict(),
            "head": self.head.to_dict(),
            "right_neck": self.right_neck.to_dict(),
            "right_shoulder": self.right_shoulder.to_dict(),
            "neckline_price": self.neckline_price,
            "confirmed": self.confirmed,
            "score": self.score,
            "trend_label": self.trend_label,
            "reasons": self.reasons,
            "qtr": self.qtr,
            "break_time": self.break_time.isoformat() if self.break_time is not None else None,
            "break_price": self.break_price,
            "retest_time": self.retest_time.isoformat() if self.retest_time is not None else None,
            "retest_price": self.retest_price,
            "message": self.message,
            "pattern_score": self.pattern_score,
            "pattern_raw_score": self.pattern_raw_score,
            "pattern_grade": self.pattern_grade,
            "pattern_caps": self.pattern_caps,
            "pattern_sections": self.pattern_sections,
            "pattern_metrics": self.pattern_metrics,
        }


def add_ma_columns(df: pd.DataFrame, config: HeadShoulderTopConfig) -> pd.DataFrame:
    df = df.copy()
    periods = sorted(set([*config.ma_periods, config.ma_short, config.ma_mid, config.ma_long]))
    for period in periods:
        df[f"ma{period}"] = df["close"].rolling(period).mean()
    return df


def add_macd_columns(df: pd.DataFrame, config: HeadShoulderTopConfig) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df["close"].ewm(span=config.macd_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=config.macd_slow, adjust=False).mean()
    df["macd_dif"] = ema_fast - ema_slow
    df["macd_dea"] = df["macd_dif"].ewm(span=config.macd_signal, adjust=False).mean()
    df["macd_hist"] = 2 * (df["macd_dif"] - df["macd_dea"])
    return df


def find_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[PivotPoint]:
    pivots: list[PivotPoint] = []
    for i in range(left, len(df) - right):
        high = df.loc[i, "high"]
        low = df.loc[i, "low"]
        is_high = high >= df.loc[i - left : i - 1, "high"].max() and high >= df.loc[i + 1 : i + right, "high"].max()
        is_low = low <= df.loc[i - left : i - 1, "low"].min() and low <= df.loc[i + 1 : i + right, "low"].min()
        if is_high:
            pivots.append(PivotPoint(index=i, time=df.loc[i, "datetime"], price=float(high), kind="high"))
        if is_low:
            pivots.append(PivotPoint(index=i, time=df.loc[i, "datetime"], price=float(low), kind="low"))
    pivots.sort(key=lambda x: x.index)
    return pivots


def compress_pivots(pivots: list[PivotPoint]) -> list[PivotPoint]:
    if not pivots:
        return []

    compressed: list[PivotPoint] = []
    current_group: list[PivotPoint] = [pivots[0]]
    current_kind = pivots[0].kind

    def best_point(group: list[PivotPoint]) -> PivotPoint:
        if group[0].kind == "high":
            return max(group, key=lambda point: (point.price, -point.index))
        return min(group, key=lambda point: (point.price, point.index))

    for pivot in pivots[1:]:
        if pivot.kind == current_kind:
            current_group.append(pivot)
            continue
        compressed.append(best_point(current_group))
        current_group = [pivot]
        current_kind = pivot.kind

    compressed.append(best_point(current_group))
    return compressed


def calculate_neckline_price(left_neck: PivotPoint, right_neck: PivotPoint, current_index: int) -> float:
    if left_neck.index == right_neck.index:
        return right_neck.price
    slope = (right_neck.price - left_neck.price) / (right_neck.index - left_neck.index)
    return left_neck.price + slope * (current_index - left_neck.index)


def calculate_true_range(df: pd.DataFrame, index: int) -> float:
    high = float(df.loc[index, "high"])
    low = float(df.loc[index, "low"])
    if index <= 0:
        return high - low
    previous_close = float(df.loc[index - 1, "close"])
    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    )


def calculate_qtr(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    right_neck: PivotPoint,
) -> float:
    start_index = min(left_neck.index, right_neck.index)
    end_index = max(left_neck.index, right_neck.index)
    true_ranges = [
        calculate_true_range(df, index)
        for index in range(start_index, end_index + 1)
    ]
    return sum(true_ranges) / len(true_ranges)


def min_head_to_neck_height_by_price(head_price: float) -> float:
    if head_price <= 2000:
        return 5
    if head_price < 5000:
        return 8
    return 10


def _safe_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _ma_values(row: pd.Series) -> dict[int, float] | None:
    values: dict[int, float] = {}
    for period in [5, 10, 20, 30, 60]:
        value = _safe_float(row.get(f"ma{period}"))
        if value is None:
            return None
        values[period] = value
    return values


def _ma_relation_score(values: dict[int, float], bullish: bool) -> tuple[float, str]:
    periods = [5, 10, 20, 30, 60]
    favored = 0
    opposed = 0
    symbol = ">" if bullish else "<"
    for left, right in zip(periods, periods[1:]):
        if bullish:
            if values[left] > values[right]:
                favored += 1
            elif values[left] < values[right]:
                opposed += 1
        else:
            if values[left] < values[right]:
                favored += 1
            elif values[left] > values[right]:
                opposed += 1
    score = 7.5 + (favored - opposed) / 4 * 7.5
    score = max(0.0, min(15.0, score))
    direction = "多头排列" if bullish else "空头排列"
    return score, f"均线排列目标为{direction} MA5 {symbol} MA10 {symbol} MA20 {symbol} MA30 {symbol} MA60：{score:.1f}/15"


def _slope_lookback(index: int) -> int | None:
    if index >= 5:
        return 5
    if index >= 3:
        return 3
    return None


def _ma_slope_score(df: pd.DataFrame, index: int, bullish: bool) -> tuple[float, str]:
    lookback = _slope_lookback(index)
    if lookback is None:
        return 0.0, "均线斜率数据不足：0/10"

    row = df.loc[index]
    prev = df.loc[index - lookback]
    periods = (10, 20)
    current = [_safe_float(row.get(f"ma{period}")) for period in periods]
    previous = [_safe_float(prev.get(f"ma{period}")) for period in periods]
    if any(value is None for value in [*current, *previous]):
        period_names = "/".join(f"MA{period}" for period in periods)
        return 0.0, f"{period_names} 斜率数据不足：0/10"

    first_current, second_current = current
    first_previous, second_previous = previous
    assert first_current is not None and second_current is not None
    assert first_previous is not None and second_previous is not None
    first_ok = first_current > first_previous if bullish else first_current < first_previous
    second_ok = second_current > second_previous if bullish else second_current < second_previous
    first_opposed = first_current < first_previous if bullish else first_current > first_previous
    second_opposed = second_current < second_previous if bullish else second_current > second_previous

    if first_ok and second_ok:
        score = 10.0
    elif first_ok:
        score = 7.5 if bullish else 5.0
    elif second_ok:
        score = 5.0 if bullish else 7.5
    elif not first_opposed and not second_opposed:
        score = 5.0
    else:
        score = 0.0
    direction = "向上" if bullish else "向下"
    period_names = "/".join(f"MA{period}" for period in periods)
    return score, f"{period_names} 斜率目标为{direction}，回看 {lookback} 根K线：{score:.1f}/10"


def _price_location_score(row: pd.Series, values: dict[int, float], bullish: bool) -> tuple[float, str]:
    close = float(row["close"])
    periods = [5, 10, 20, 30, 60]
    matched = [
        period
        for period in periods
        if (close > values[period] if bullish else close < values[period])
    ]
    score = float(len(matched) * 2)
    side = "上方" if bullish else "下方"
    return score, f"收盘价位于 {len(matched)}/5 条跟踪均线{side}：{score:.1f}/10"


def _is_full_alignment(values: dict[int, float], bullish: bool) -> bool:
    periods = [5, 10, 20, 30, 60]
    if bullish:
        return all(values[left] > values[right] for left, right in zip(periods, periods[1:]))
    return all(values[left] < values[right] for left, right in zip(periods, periods[1:]))


def _ma_bandwidth(values: dict[int, float]) -> float:
    denominator = abs(values[60])
    if denominator == 0:
        denominator = 1.0
    return (max(values.values()) - min(values.values())) / denominator


def _ma_bandwidth_score(df: pd.DataFrame, index: int, values: dict[int, float], bullish: bool) -> tuple[float, str]:
    lookback = _slope_lookback(index)
    if lookback is None:
        return 0.0, "均线带宽数据不足：0/10"
    prev_values = _ma_values(df.loc[index - lookback])
    if prev_values is None:
        return 0.0, "均线带宽对比数据不足：0/10"

    current_width = _ma_bandwidth(values)
    previous_width = _ma_bandwidth(prev_values)
    expanding = current_width > previous_width
    target_aligned = _is_full_alignment(values, bullish)
    opposite_aligned = _is_full_alignment(values, not bullish)

    if target_aligned and expanding:
        score = 10.0
        state = "目标趋势排列且均线带宽扩大"
    elif target_aligned and not expanding:
        score = 5.0
        state = "目标趋势排列但均线带宽收窄"
    elif opposite_aligned and expanding:
        score = 0.0
        state = "反向趋势排列且均线带宽扩大"
    elif opposite_aligned and not expanding:
        score = 2.5
        state = "反向趋势排列但均线带宽收窄"
    else:
        score = 5.0
        state = "均线排列混合，偏震荡"
    return score, f"均线带宽：{state}：{score:.1f}/10"


def calculate_ma_trend_score(df: pd.DataFrame, index: int, bullish: bool) -> tuple[int, list[str]]:
    if index < 0 or index >= len(df):
        return 0, ["均线评分位置超出数据范围"]

    row = df.loc[index]
    values = _ma_values(row)
    if values is None:
        return 0, ["MA5/MA10/MA20/MA30/MA60 数据不足：0/50"]

    reasons: list[str] = []
    total = 0.0
    for score, reason in [
        _ma_relation_score(values, bullish),
        _ma_slope_score(df, index, bullish),
        _price_location_score(row, values, bullish),
        _ma_bandwidth_score(df, index, values, bullish),
    ]:
        total += score
        reasons.append(reason)

    close = float(row["close"])
    above_or_below_ma60 = close > values[60] if bullish else close < values[60]
    ma60_score = 5.0 if above_or_below_ma60 else 0.0
    side = "站上" if bullish else "跌破"
    reasons.append(f"收盘价{side} MA60 确认项：{ma60_score:.1f}/5")
    total += ma60_score

    return int(round(total)), reasons
def _timeframe_score_index(score_df: pd.DataFrame, signal_time: pd.Timestamp) -> int | None:
    if len(score_df) == 0:
        return None
    datetimes = pd.to_datetime(score_df["datetime"])
    matching = score_df.index[datetimes <= signal_time]
    if len(matching) == 0:
        return None
    return int(matching[-1])


def _prepare_ma_score_df(df: pd.DataFrame) -> pd.DataFrame:
    score_df = df.copy().reset_index(drop=True)
    score_df["datetime"] = pd.to_datetime(score_df["datetime"])
    return add_ma_columns(score_df, HeadShoulderTopConfig())


def _score_named_timeframe(
    df: pd.DataFrame | None,
    name: str,
    bullish: bool,
    signal_time: pd.Timestamp | None,
) -> tuple[int, list[str]]:
    if df is None:
        name_cn = "小时线" if name == "Hourly" else "日线"
        return 0, [f"{name_cn}评分数据不可用：0/50"]
    score_df = _prepare_ma_score_df(df)
    score_index = len(score_df) - 1 if signal_time is None else _timeframe_score_index(score_df, signal_time)
    if score_index is None:
        name_cn = "小时线" if name == "Hourly" else "日线"
        return 0, [f"{name_cn}在信号时间前没有可用K线：0/50"]
    score, reasons = calculate_ma_trend_score(score_df, score_index, bullish=bullish)
    name_cn = "小时线" if name == "Hourly" else "日线"
    return score, [f"{name_cn}评分：{score}/50", *reasons]


def _normalized_zone(lower: float, upper: float) -> tuple[float, float] | None:
    if not math.isfinite(lower) or not math.isfinite(upper):
        return None
    if lower <= 0 and upper <= 0:
        return None
    return min(lower, upper), max(lower, upper)


def _hourly_key_zone_trend_override(
    hourly_df: pd.DataFrame | None,
    bullish: bool,
    head_time: pd.Timestamp | None,
    config: HeadShoulderTopConfig | None,
) -> tuple[int, list[str]] | None:
    if hourly_df is None or head_time is None or config is None or not config.enable_key_zone_trend_score:
        return None

    zone = (
        _normalized_zone(config.support_zone_min, config.support_zone_max)
        if bullish
        else _normalized_zone(config.resistance_zone_min, config.resistance_zone_max)
    )
    if zone is None:
        return None

    score_df = hourly_df.copy().reset_index(drop=True)
    score_df["datetime"] = pd.to_datetime(score_df["datetime"])
    head_hour_index = _timeframe_score_index(score_df, head_time)
    if head_hour_index is None:
        return None

    start = max(0, head_hour_index - 5)
    end = min(len(score_df) - 1, head_hour_index + 5)
    window = score_df.loc[start:end]
    zone_min, zone_max = zone
    touched = bool(((window["high"].astype(float) >= zone_min) & (window["low"].astype(float) <= zone_max)).any())
    if not touched:
        return None

    label = "支撑区间" if bullish else "阻挡区间"
    return 50, ["小时线评分：50/50", f"头部所在小时线前后各5根触碰{label} {zone_min:.4f}-{zone_max:.4f}，小时线趋势评分直接满分"]


def calculate_combined_trend_score(
    hourly_df: pd.DataFrame | None,
    bullish: bool,
    signal_time: pd.Timestamp | None = None,
    daily_df: pd.DataFrame | None = None,
    config: HeadShoulderTopConfig | None = None,
    head_time: pd.Timestamp | None = None,
) -> tuple[int, list[str]]:
    hourly_override = _hourly_key_zone_trend_override(hourly_df, bullish, head_time, config)
    if hourly_override is not None:
        hourly_score, hourly_reasons = hourly_override
    else:
        hourly_score, hourly_reasons = _score_named_timeframe(hourly_df, "Hourly", bullish, signal_time)
    daily_score, daily_reasons = _score_named_timeframe(daily_df, "Daily", bullish, signal_time)
    return hourly_score + daily_score, [*hourly_reasons, *daily_reasons]


def trend_label_from_score(score: int, bullish: bool) -> str:
    bullish_labels = [
        (80, "强多头趋势"),
        (65, "多头趋势"),
        (55, "多头趋势下震荡"),
        (40, "震荡趋势"),
        (25, "空头趋势下震荡"),
        (0, "空头趋势"),
    ]
    bearish_labels = [
        (80, "强空头趋势"),
        (65, "空头趋势"),
        (55, "空头趋势下震荡"),
        (40, "震荡趋势"),
        (25, "多头趋势下震荡"),
        (0, "多头趋势"),
    ]
    for threshold, label in (bullish_labels if bullish else bearish_labels):
        if score >= threshold:
            return label
    return "震荡趋势"


def _pattern_item(label: str, score: int, max_score: int, detail: str) -> dict[str, Any]:
    return {
        "label": label,
        "score": int(score),
        "max": int(max_score),
        "detail": detail,
    }


def _pattern_section(key: str, title: str, max_score: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "max": int(max_score),
        "score": int(sum(item["score"] for item in items)),
        "items": items,
    }


def _score_by_thresholds(value: float | None, thresholds: list[tuple[float, int]], default: int = 0) -> int:
    if value is None or not math.isfinite(value):
        return default
    for limit, score in thresholds:
        if value <= limit:
            return score
    return default


def _score_by_min_thresholds(value: float | None, thresholds: list[tuple[float, int]], default: int = 0) -> int:
    if value is None or not math.isfinite(value):
        return default
    for limit, score in thresholds:
        if value >= limit:
            return score
    return default


def _scale_score(score: int, old_max: int, new_max: int) -> int:
    if old_max <= 0:
        return 0
    return int(round(score / old_max * new_max))


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _pattern_grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "忽略"


def _pattern_movement(start: float, end: float, inverse: bool) -> float:
    return end - start if inverse else start - end


def _local_trend_score(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    head: PivotPoint,
    inverse: bool,
) -> tuple[list[dict[str, Any]], bool]:
    lookback = min(20, left_shoulder.index)
    if lookback <= 1:
        clear_score = 0
        clear_detail = "LS 前数据不足，无法确认前置趋势"
        trend_clear = False
    else:
        start_index = left_shoulder.index - lookback
        prior_close = float(df.loc[start_index, "close"])
        head_close = float(df.loc[head.index, "close"])
        move = prior_close - head_close if inverse else head_close - prior_close
        prior_range = max(
            float(df.loc[start_index:left_shoulder.index, "high"].max())
            - float(df.loc[start_index:left_shoulder.index, "low"].min()),
            1e-9,
        )
        directional_bars = 0
        closes = df.loc[start_index:head.index, "close"].astype(float).tolist()
        for prev, current in zip(closes, closes[1:]):
            if (current < prev if inverse else current > prev):
                directional_bars += 1
        ratio = directional_bars / max(1, len(closes) - 1)
        trend_clear = move > 0 and (abs(move) >= prior_range * 0.25 or ratio >= 0.55)
        clear_score = 5 if trend_clear else 0
        direction = "下跌" if inverse else "上涨"
        clear_detail = (
            f"启发式：LS 前 {lookback} 根到头部的局部{direction}推进为 {move:.4f}，"
            f"方向K线占比 {ratio:.2f}"
        )

    span = max(0, head.index - max(0, left_shoulder.index - lookback))
    if span >= 12:
        duration_score = 3
    elif span >= 6:
        duration_score = 2
    elif span >= 3:
        duration_score = 1
    else:
        duration_score = 0
    duration_detail = f"启发式：前置趋势观察跨度 {span} 根K线"

    key_score = 0
    key_evidence: list[str] = []
    prior_start = max(0, left_shoulder.index - max(lookback, 20))
    if prior_start < left_shoulder.index:
        prior_high = float(df.loc[prior_start:left_shoulder.index, "high"].max())
        prior_low = float(df.loc[prior_start:left_shoulder.index, "low"].min())
        if inverse:
            if head.price <= prior_low:
                key_score = max(key_score, 1)
                key_evidence.append("头部接近/刷新前低")
        else:
            if head.price >= prior_high:
                key_score = max(key_score, 1)
                key_evidence.append("头部接近/刷新前高")
    row = df.loc[head.index]
    ma_hits = 0
    for period in (60, 250):
        value = _safe_float(row.get(f"ma{period}"))
        if value is None:
            continue
        tolerance = abs(head.price) * 0.01
        if abs(head.price - value) <= tolerance:
            ma_hits += 1
            key_evidence.append(f"靠近 MA{period}")
    key_score = max(key_score, min(2, ma_hits))
    key_detail = "启发式：" + ("，".join(key_evidence) if key_evidence else "未观察到前高/前低或中长期均线配合")

    return [
        _pattern_item("前置趋势明确", clear_score, 5, clear_detail),
        _pattern_item("趋势具有持续性", duration_score, 3, duration_detail),
        _pattern_item("关键位置配合", key_score, 2, key_detail),
    ], trend_clear


def _pattern_structure_items(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    head: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    inverse: bool,
    qtr: float,
    qtr_anomaly: bool,
) -> list[dict[str, Any]]:
    higher_shoulder = min(left_shoulder.price, right_shoulder.price) if inverse else max(left_shoulder.price, right_shoulder.price)
    head_excess = higher_shoulder - head.price if inverse else head.price - higher_shoulder
    head_excess_qtr = _safe_ratio(head_excess, qtr) if not qtr_anomaly else None
    head_score = _score_by_min_thresholds(head_excess_qtr, [(1.5, 4), (1.0, 3), (0.5, 2), (0.0, 1)])

    ds = abs(left_shoulder.price - right_shoulder.price)
    ds_qtr = _safe_ratio(ds, qtr) if not qtr_anomaly else None
    shoulder_score = _score_by_thresholds(ds_qtr, [(0.5, 10), (1.0, 8), (1.5, 5), (2.0, 2)])

    left_denominator = left_neck.price - head.price if inverse else head.price - left_neck.price
    left_numerator = left_neck.price - left_shoulder.price if inverse else left_shoulder.price - left_neck.price
    right_denominator = right_neck.price - head.price if inverse else head.price - right_neck.price
    right_numerator = right_neck.price - right_shoulder.price if inverse else right_shoulder.price - right_neck.price
    left_height_ratio = _safe_ratio(left_numerator, left_denominator) if left_denominator > 0 and left_numerator >= 0 else None
    right_height_ratio = _safe_ratio(right_numerator, right_denominator) if right_denominator > 0 and right_numerator >= 0 else None

    def shoulder_height_score(ratio: float | None) -> int:
        if ratio is None:
            return 0
        if 0.45 <= ratio <= 0.75:
            return 4
        if 0.35 <= ratio < 0.45 or 0.75 < ratio <= 0.85:
            return 3
        if 0.25 <= ratio < 0.35 or 0.85 < ratio <= 0.95:
            return 2
        if 0.15 <= ratio < 0.25 or 0.95 < ratio < 1.0:
            return 1
        return 0

    noise = 0
    same_kind = "low" if inverse else "high"
    for index in range(left_shoulder.index + 1, right_shoulder.index):
        if index in {left_neck.index, head.index, right_neck.index}:
            continue
        if inverse:
            if float(df.loc[index, "low"]) < min(left_shoulder.price, right_shoulder.price, head.price):
                noise += 1
        else:
            if float(df.loc[index, "high"]) > max(left_shoulder.price, right_shoulder.price, head.price):
                noise += 1
    noise_score = 2 if noise == 0 else 1 if noise <= 1 else 0

    return [
        _pattern_item("头部突出度", head_score, 4, f"头部超出较高肩 {head_excess:.4f}，约 {head_excess_qtr if head_excess_qtr is not None else 0:.2f} QTR"),
        _pattern_item("左右肩高度接近", shoulder_score, 10, f"DS={ds:.4f}，DS/QTR={ds_qtr if ds_qtr is not None else 0:.2f}"),
        _pattern_item("左肩有效高度", shoulder_height_score(left_height_ratio), 4, f"左肩到左颈高度占头部到左颈高度 {left_height_ratio if left_height_ratio is not None else 0:.2f}"),
        _pattern_item("右肩有效高度", shoulder_height_score(right_height_ratio), 4, f"右颈到右肩高度占头部到右颈高度 {right_height_ratio if right_height_ratio is not None else 0:.2f}"),
        _pattern_item("中间杂峰/杂谷较少", noise_score, 2, f"启发式：{same_kind} 方向破坏性杂点数量 {noise}"),
    ]


def _pattern_neckline_items(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    inverse: bool,
    qtr: float,
    qtr_anomaly: bool,
) -> list[dict[str, Any]]:
    dn = abs(left_neck.price - right_neck.price)
    dn_qtr = _safe_ratio(dn, qtr) if not qtr_anomaly else None
    close_score = _score_by_thresholds(dn_qtr, [(0.5, 10), (1.0, 8), (1.5, 5), (2.0, 2)])

    clarity = 0
    for point in (left_neck, right_neck):
        left = max(0, point.index - 2)
        right = min(len(df) - 1, point.index + 2)
        if inverse:
            if point.price >= float(df.loc[left:right, "high"].max()):
                clarity += 1
        else:
            if point.price <= float(df.loc[left:right, "low"].min()):
                clarity += 1
    clarity_score = 4 if clarity == 2 else 2 if clarity == 1 else 0

    pierces = 0
    tolerance = qtr * 0.25 if not qtr_anomaly else 0
    for index in range(left_neck.index + 1, min(right_shoulder.index, len(df) - 1) + 1):
        neckline = calculate_neckline_price(left_neck, right_neck, index)
        close = float(df.loc[index, "close"])
        if inverse:
            if close > neckline + tolerance:
                pierces += 1
        else:
            if close < neckline - tolerance:
                pierces += 1
    respect_score = 2 if pierces == 0 else 1 if pierces <= 1 else 0

    return [
        _pattern_item("左右颈价格接近", close_score, 10, f"DN={dn:.4f}，DN/QTR={dn_qtr if dn_qtr is not None else 0:.2f}"),
        _pattern_item("N1/N2 拐点清晰", clarity_score, 4, f"启发式：两侧 2 根K线局部极值命中 {clarity}/2"),
        _pattern_item("价格尊重颈线区域", respect_score, 2, f"启发式：形成阶段无效穿越次数 {pierces}"),
    ]


def _pattern_time_items(
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    head: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
) -> tuple[list[dict[str, Any]], float, float]:
    left_span = max(1, head.index - left_shoulder.index)
    right_span = max(1, right_shoulder.index - head.index)
    ts = max(left_span, right_span) / min(left_span, right_span)
    left_neck_span = max(1, head.index - left_neck.index)
    right_neck_span = max(1, right_neck.index - head.index)
    tn = max(left_neck_span, right_neck_span) / min(left_neck_span, right_neck_span)
    ts_score = _score_by_thresholds(ts, [(1.5, 8), (2.0, 7), (2.5, 4), (3.0, 1)])
    tn_score = _score_by_thresholds(tn, [(1.5, 6), (2.0, 4), (3.0, 2)])
    return [
        _pattern_item("肩部时间比例 TS", ts_score, 8, f"TS={ts:.2f}，左右跨度 {left_span}/{right_span} 根"),
        _pattern_item("颈点时间比例 TN", tn_score, 6, f"TN={tn:.2f}，左右跨度 {left_neck_span}/{right_neck_span} 根"),
    ], ts, tn


def _macd_value(df: pd.DataFrame, index: int) -> float | None:
    for column in ("macd_hist", "macd_dif"):
        if column in df.columns:
            value = _safe_float(df.loc[index].get(column))
            if value is not None:
                return value
    return None


def _mean_true_range(df: pd.DataFrame, start_index: int, end_index: int) -> float | None:
    start_index = max(0, start_index)
    end_index = min(len(df) - 1, end_index)
    if start_index > end_index:
        return None
    values = [calculate_true_range(df, index) for index in range(start_index, end_index + 1)]
    if not values:
        return None
    return sum(values) / len(values)


def _pattern_momentum_items(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    head: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    inverse: bool,
    trigger_index: int,
) -> tuple[list[dict[str, Any]], bool]:
    left_macd = _macd_value(df, left_shoulder.index)
    head_macd = _macd_value(df, head.index)
    if left_macd is None or head_macd is None:
        exhaustion_score = 0
        exhaustion_detail = "MACD 数据不足，头部衰竭项记 0"
    else:
        exhausted = head_macd > left_macd if inverse else head_macd < left_macd
        exhaustion_score = 5 if exhausted else 0
        exhaustion_detail = f"MACD 启发式背离：LS={left_macd:.4f}，H={head_macd:.4f}"

    head_leg = abs(head.price - left_neck.price) / max(1, head.index - left_neck.index)
    right_leg = abs(right_shoulder.price - right_neck.price) / max(1, right_shoulder.index - right_neck.index)
    right_weaker = right_leg < head_leg
    right_clearly_stronger = right_leg > head_leg * 1.15
    right_weaker_score = 4 if right_weaker else 0
    right_weaker_detail = f"头部推进速度 {head_leg:.4f}/bar，右肩推进速度 {right_leg:.4f}/bar"

    volume_series = df["volume"] if "volume" in df.columns else pd.Series(dtype=float)
    volume_reliable = (
        not volume_series.empty
        and not volume_series.loc[max(0, left_neck.index):min(len(df) - 1, right_shoulder.index)].isna().all()
        and float(volume_series.fillna(0).sum()) > 0
    )
    if volume_reliable:
        head_volume = float(df.loc[left_neck.index:head.index, "volume"].mean())
        right_volume = float(df.loc[right_neck.index:right_shoulder.index, "volume"].mean())
        volume_contracting = right_volume < head_volume
        volume_score = 3 if volume_contracting else 0
        volume_detail = f"右肩均量 {right_volume:.2f} vs 头部阶段均量 {head_volume:.2f}"
    else:
        head_volatility = _mean_true_range(df, left_neck.index, head.index)
        right_volatility = _mean_true_range(df, right_neck.index, right_shoulder.index)
        volume_contracting = (
            head_volatility is not None
            and right_volatility is not None
            and right_volatility < head_volatility
        )
        volume_score = 3 if volume_contracting else 0
        volume_detail = (
            "成交量缺失/为0，启发式改用波动率代理："
            f"右肩TR {right_volatility if right_volatility is not None else 0:.4f} vs 头部TR {head_volatility if head_volatility is not None else 0:.4f}"
        )

    close_move = _pattern_movement(float(df.loc[right_shoulder.index, "close"]), float(df.loc[trigger_index, "close"]), inverse)
    bars = max(1, trigger_index - right_shoulder.index)
    trigger_speed = close_move / bars
    ma_help = False
    if "ma5" in df.columns and trigger_index >= 3:
        now = _safe_float(df.loc[trigger_index].get("ma5"))
        prev = _safe_float(df.loc[trigger_index - min(3, trigger_index)].get("ma5"))
        if now is not None and prev is not None:
            ma_help = now > prev if inverse else now < prev
    trigger_momentum_score = 3 if close_move > 0 and (ma_help or trigger_speed > 0) else 1 if close_move > 0 else 0
    trigger_momentum_detail = f"RS 到触发收盘推进 {close_move:.4f}，MA5 方向配合={ma_help}"

    return [
        _pattern_item("头部出现动能衰竭", exhaustion_score, 5, exhaustion_detail),
        _pattern_item("右肩动能弱于头部", right_weaker_score, 4, right_weaker_detail),
        _pattern_item("右肩成交量收缩", volume_score, 3, volume_detail),
        _pattern_item("右肩至半程方向动能", trigger_momentum_score, 3, trigger_momentum_detail),
    ], right_clearly_stronger


def _pattern_trigger_items(
    right_shoulder: PivotPoint,
    inverse: bool,
    trigger_index: int,
    trigger_price: float,
    midpoint: float,
) -> tuple[list[dict[str, Any]], int]:
    trigger_speed_bars = max(0, trigger_index - right_shoulder.index)
    if trigger_speed_bars <= 5:
        speed_score = 3
    elif trigger_speed_bars <= 10:
        speed_score = 2
    elif trigger_speed_bars <= 20:
        speed_score = 1
    else:
        speed_score = 0
    reached = trigger_price >= midpoint if inverse else trigger_price <= midpoint
    return [
        _pattern_item("收盘价触及半程", 4 if reached else 0, 4, f"收盘触发价 {trigger_price:.4f}，半程价 {midpoint:.4f}"),
        _pattern_item("触发速度", speed_score, 3, f"RS 后 {trigger_speed_bars} 根K线触发"),
    ], trigger_speed_bars


def _pattern_trade_value_items(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    head: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    inverse: bool,
    qtr: float,
    qtr_anomaly: bool,
    trigger_index: int,
    trigger_price: float,
) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    neckline_at_head = calculate_neckline_price(left_neck, right_neck, head.index)
    neckline_at_trigger = calculate_neckline_price(left_neck, right_neck, trigger_index)
    ph = neckline_at_head - head.price if inverse else head.price - neckline_at_head
    if qtr_anomaly or ph <= 0:
        stop = None
        target = None
        risk = None
        reward = None
        rr = 0.0
        ph_qtr = None
    else:
        stop = right_shoulder.price - 0.25 * qtr if inverse else right_shoulder.price + 0.25 * qtr
        target = neckline_at_trigger + ph if inverse else neckline_at_trigger - ph
        risk = abs(trigger_price - stop)
        reward = target - trigger_price if inverse else trigger_price - target
        rr = reward / risk if risk > 0 and reward > 0 else 0.0
        ph_qtr = ph / qtr

    rr_base_score = _score_by_min_thresholds(rr, [(3.0, 6), (2.0, 5), (1.5, 3), (1.2, 1)])
    ph_base_score = _score_by_min_thresholds(ph_qtr, [(4.0, 2), (2.5, 1)])
    rr_score = _scale_score(rr_base_score, 6, 8)
    ph_score = _scale_score(ph_base_score, 2, 4)

    obstacles = 0
    if target is not None:
        start = min(trigger_index, len(df) - 1)
        lookback_start = max(0, left_neck.index - 20)
        historical = df.loc[lookback_start:start]
        tolerance = qtr * 0.5 if not qtr_anomaly else abs(trigger_price) * 0.005
        if inverse:
            path_low, path_high = sorted((trigger_price, target))
            obstacles = int(((historical["high"] >= path_low - tolerance) & (historical["high"] <= path_high + tolerance)).sum())
        else:
            path_low, path_high = sorted((target, trigger_price))
            obstacles = int(((historical["low"] >= path_low - tolerance) & (historical["low"] <= path_high + tolerance)).sum())
    obstacle_score = 2 if obstacles <= 1 else 1 if obstacles <= 3 else 0

    return [
        _pattern_item(
            "预期盈亏比 RR",
            rr_score,
            8,
            f"RR={rr:.2f}，触发价={trigger_price:.4f}，止损价={stop if stop is not None else 0:.4f}，"
            f"目标价={target if target is not None else 0:.4f}，Risk={risk if risk is not None else 0:.4f}，"
            f"Reward={reward if reward is not None else 0:.4f}",
        ),
        _pattern_item("形态高度/波动率", ph_score, 4, f"PH={ph:.4f}，PH/QTR={ph_qtr if ph_qtr is not None else 0:.2f}"),
        _pattern_item("目标路径障碍", obstacle_score, 2, f"启发式：触发价到目标价之间历史支撑/压力命中 {obstacles} 次"),
    ], {
        "neckline_at_head": neckline_at_head,
        "neckline_at_trigger": neckline_at_trigger,
        "ph": ph,
        "ph_qtr": ph_qtr,
        "stop": stop,
        "target": target,
        "risk": risk,
        "reward": reward,
        "rr": rr,
    }


def calculate_pattern_score(
    df: pd.DataFrame,
    *,
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    head: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    inverse: bool,
    qtr: float,
    trigger_index: int,
    trigger_price: float,
    midpoint: float,
) -> dict[str, Any]:
    qtr_anomaly = qtr <= 0 or not math.isfinite(qtr)
    ds = abs(left_shoulder.price - right_shoulder.price)
    dn = abs(left_neck.price - right_neck.price)
    ds_qtr = _safe_ratio(ds, qtr) if not qtr_anomaly else None
    dn_qtr = _safe_ratio(dn, qtr) if not qtr_anomaly else None

    trend_items, trend_clear = _local_trend_score(df, left_shoulder, head, inverse)
    structure_items = _pattern_structure_items(
        df, left_shoulder, left_neck, head, right_neck, right_shoulder, inverse, qtr, qtr_anomaly
    )
    neckline_items = _pattern_neckline_items(df, left_neck, right_neck, right_shoulder, inverse, qtr, qtr_anomaly)
    time_items, ts, tn = _pattern_time_items(left_shoulder, left_neck, head, right_neck, right_shoulder)
    momentum_items, _ = _pattern_momentum_items(
        df, left_shoulder, left_neck, head, right_neck, right_shoulder, inverse, trigger_index
    )
    trigger_items, trigger_speed_bars = _pattern_trigger_items(
        right_shoulder, inverse, trigger_index, trigger_price, midpoint
    )
    trade_items, trade_metrics = _pattern_trade_value_items(
        df, left_neck, head, right_neck, right_shoulder, inverse, qtr, qtr_anomaly, trigger_index, trigger_price
    )

    sections = [
        _pattern_section("trend", "趋势背景", 10, trend_items),
        _pattern_section("structure", "三峰/三谷结构", 24, structure_items),
        _pattern_section("neckline", "颈线质量", 16, neckline_items),
        _pattern_section("time", "时间对称性", 14, time_items),
        _pattern_section("momentum", "量能/动能配合", 15, momentum_items),
        _pattern_section("trigger", "右肩与半程触发质量", 7, trigger_items),
        _pattern_section("trade_value", "即时交易价值", 14, trade_items),
    ]
    raw_score = int(sum(section["score"] for section in sections))

    caps: list[int] = []
    if not trend_clear:
        caps.append(75)
    if ds_qtr is not None and ds_qtr > 2.0:
        caps.append(75)
    if dn_qtr is not None and dn_qtr > 2.0:
        caps.append(75)
    final_score = min([raw_score, *caps]) if caps else raw_score

    metrics: dict[str, Any] = {
        "ds": ds,
        "dn": dn,
        "ds_qtr": ds_qtr,
        "dn_qtr": dn_qtr,
        "ts": ts,
        "tn": tn,
        "qtr": qtr,
        "qtr_anomaly": qtr_anomaly,
        "trigger_index": trigger_index,
        "trigger_price": trigger_price,
        "midpoint": midpoint,
        "trigger_speed_bars": trigger_speed_bars,
        **trade_metrics,
    }

    return {
        "final_score": int(final_score),
        "raw_score": int(raw_score),
        "grade": _pattern_grade(int(final_score)),
        "caps": caps,
        "sections": sections,
        "metrics": metrics,
    }


def _pattern_quality_allows_alert(pattern_result: dict[str, Any], config: HeadShoulderTopConfig) -> bool:
    if not config.enable_score or config.min_pattern_score_to_alert <= 0:
        return True
    return int(pattern_result["final_score"]) >= config.min_pattern_score_to_alert


def passes_head_neck_bar_limit(
    timeframe: str,
    left_neck: "PivotPoint",
    head: "PivotPoint",
    right_neck: "PivotPoint",
) -> bool:
    max_bars = MAX_HEAD_NECK_BARS_BY_TIMEFRAME.get(timeframe)
    left_neck_to_head_bars = head.index - left_neck.index
    head_to_right_neck_bars = right_neck.index - head.index
    if left_neck_to_head_bars <= 1 or head_to_right_neck_bars <= 1:
        return False
    if max_bars is None:
        return True
    return (
        left_neck_to_head_bars < max_bars
        and head_to_right_neck_bars < max_bars
    )


def passes_one_minute_head_neck_bar_limit(
    timeframe: str,
    left_neck: "PivotPoint",
    head: "PivotPoint",
    right_neck: "PivotPoint",
) -> bool:
    return passes_head_neck_bar_limit(timeframe, left_neck, head, right_neck)


def iter_pattern_candidates(
    pivots: list[PivotPoint],
    kinds: list[str],
) -> list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]]:
    candidates: list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]] = []
    seen: set[tuple[int, int, int, int, int]] = set()

    def span_preserves_swing(start_pos: int, end_pos: int, start: PivotPoint, end: PivotPoint) -> bool:
        skipped = pivots[start_pos + 1 : end_pos]
        if not skipped:
            return True
        if start.kind == "high" and end.kind == "low":
            return all(point.price <= start.price if point.kind == "high" else point.price >= end.price for point in skipped)
        if start.kind == "low" and end.kind == "high":
            return all(point.price >= start.price if point.kind == "low" else point.price <= end.price for point in skipped)
        return False

    for left_shoulder_index in range(len(pivots) - 4):
        left_shoulder = pivots[left_shoulder_index]
        if left_shoulder.kind != kinds[0]:
            continue

        for left_neck_index in range(left_shoulder_index + 1, len(pivots) - 3):
            left_neck = pivots[left_neck_index]
            if left_neck.kind != kinds[1] or not span_preserves_swing(left_shoulder_index, left_neck_index, left_shoulder, left_neck):
                continue

            for head_index in range(left_neck_index + 1, len(pivots) - 2):
                head = pivots[head_index]
                if head.kind != kinds[2] or not span_preserves_swing(left_neck_index, head_index, left_neck, head):
                    continue
                for right_neck_index in range(head_index + 1, len(pivots) - 1):
                    right_neck = pivots[right_neck_index]
                    if right_neck.kind != kinds[3] or not span_preserves_swing(head_index, right_neck_index, head, right_neck):
                        continue
                    for right_shoulder_index in range(right_neck_index + 1, len(pivots)):
                        right_shoulder = pivots[right_shoulder_index]
                        if (
                            right_shoulder.kind != kinds[4]
                            or not span_preserves_swing(right_neck_index, right_shoulder_index, right_neck, right_shoulder)
                        ):
                            continue
                        key = (left_shoulder.index, left_neck.index, head.index, right_neck.index, right_shoulder.index)
                        if key in seen:
                            continue
                        seen.add(key)
                        candidates.append((left_shoulder, left_neck, head, right_neck, right_shoulder))

    return candidates


def iter_pattern_candidates_with_right_shoulder_pivots(
    structure_pivots: list[PivotPoint],
    right_shoulder_pivots: list[PivotPoint],
    kinds: list[str],
) -> list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]]:
    candidates: list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]] = []
    seen: set[tuple[int, int, int, int, int]] = set()

    def structure_span_preserves_swing(start_pos: int, end_pos: int, start: PivotPoint, end: PivotPoint) -> bool:
        skipped = structure_pivots[start_pos + 1 : end_pos]
        if not skipped:
            return True
        if start.kind == "high" and end.kind == "low":
            return all(point.price <= start.price if point.kind == "high" else point.price >= end.price for point in skipped)
        if start.kind == "low" and end.kind == "high":
            return all(point.price >= start.price if point.kind == "low" else point.price <= end.price for point in skipped)
        return False

    right_shoulder_candidates = [point for point in right_shoulder_pivots if point.kind == kinds[4]]
    for left_shoulder_index in range(len(structure_pivots) - 3):
        left_shoulder = structure_pivots[left_shoulder_index]
        if left_shoulder.kind != kinds[0]:
            continue

        for left_neck_index in range(left_shoulder_index + 1, len(structure_pivots) - 2):
            left_neck = structure_pivots[left_neck_index]
            if (
                left_neck.kind != kinds[1]
                or not structure_span_preserves_swing(left_shoulder_index, left_neck_index, left_shoulder, left_neck)
            ):
                continue

            for head_index in range(left_neck_index + 1, len(structure_pivots) - 1):
                head = structure_pivots[head_index]
                if (
                    head.kind != kinds[2]
                    or not structure_span_preserves_swing(left_neck_index, head_index, left_neck, head)
                ):
                    continue

                for right_neck_index in range(head_index + 1, len(structure_pivots)):
                    right_neck = structure_pivots[right_neck_index]
                    if (
                        right_neck.kind != kinds[3]
                        or not structure_span_preserves_swing(head_index, right_neck_index, head, right_neck)
                    ):
                        continue

                    for right_shoulder in right_shoulder_candidates:
                        if right_shoulder.index <= right_neck.index:
                            continue
                        if right_neck.kind == right_shoulder.kind:
                            continue
                        key = (left_shoulder.index, left_neck.index, head.index, right_neck.index, right_shoulder.index)
                        if key in seen:
                            continue
                        seen.add(key)
                        candidates.append((left_shoulder, left_neck, head, right_neck, right_shoulder))

    return candidates


def find_structure_pivots_for_timeframe(
    df: pd.DataFrame,
    timeframe: str,
    config: HeadShoulderTopConfig,
) -> list[PivotPoint]:
    if timeframe in MIXED_PIVOT_CONFIRMATION_TIMEFRAMES:
        return compress_pivots(find_pivots(df, left=STRUCTURE_PIVOT_WINDOW, right=STRUCTURE_PIVOT_WINDOW))
    return compress_pivots(find_pivots(df, left=config.pivot_left, right=config.pivot_right))


def iter_timeframe_pattern_candidates(
    df: pd.DataFrame,
    timeframe: str,
    config: HeadShoulderTopConfig,
    kinds: list[str],
    structure_pivots: list[PivotPoint] | None = None,
) -> list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]]:
    structure_pivots = structure_pivots if structure_pivots is not None else find_structure_pivots_for_timeframe(df, timeframe, config)
    if timeframe not in MIXED_PIVOT_CONFIRMATION_TIMEFRAMES:
        return iter_pattern_candidates(structure_pivots, kinds)
    right_shoulder_pivots = find_pivots(
        df,
        left=RIGHT_SHOULDER_PIVOT_WINDOW,
        right=RIGHT_SHOULDER_PIVOT_WINDOW,
    )
    return iter_pattern_candidates_with_right_shoulder_pivots(structure_pivots, right_shoulder_pivots, kinds)


def validate_candle_close_constraints(
    df: pd.DataFrame,
    points: list[PivotPoint],
    *,
    inverse: bool,
) -> tuple[bool, str]:
    if len(points) != 5:
        return False, "Expected five pattern points"

    left_shoulder, left_neck, head, right_neck, right_shoulder = points
    left_leg_closes = df.iloc[left_shoulder.index : left_neck.index + 1]["close"]
    left_head_closes = df.iloc[left_neck.index : head.index + 1]["close"]
    right_head_closes = df.iloc[head.index : right_neck.index + 1]["close"]
    right_leg_closes = df.iloc[right_neck.index : right_shoulder.index + 1]["close"]

    if inverse:
        if float(left_leg_closes.min()) < left_shoulder.price:
            return False, "Close below left shoulder price between left shoulder and left neck"
        if float(left_leg_closes.max()) > left_neck.price:
            return False, "Close above left neck price between left shoulder and left neck"
        if float(left_head_closes.min()) < head.price:
            return False, "Close below head price between left neck and head"
        if float(left_head_closes.max()) > left_neck.price:
            return False, "Close above left neck price between left neck and head"
        if float(right_head_closes.min()) < head.price:
            return False, "Close below head price between head and right neck"
        if float(right_head_closes.max()) > right_neck.price:
            return False, "Close above right neck price between head and right neck"
        if float(right_leg_closes.min()) < right_shoulder.price:
            return False, "Close below right shoulder price between right neck and right shoulder"
        if float(right_leg_closes.max()) > right_neck.price:
            return False, "Close above right neck price between right neck and right shoulder"
        return True, ""

    if float(left_leg_closes.max()) > left_shoulder.price:
        return False, "Close above left shoulder price between left shoulder and left neck"
    if float(left_leg_closes.min()) < left_neck.price:
        return False, "Close below left neck price between left shoulder and left neck"
    if float(left_head_closes.max()) > head.price:
        return False, "Close above head price between left neck and head"
    if float(left_head_closes.min()) < left_neck.price:
        return False, "Close below left neck price between left neck and head"
    if float(right_head_closes.max()) > head.price:
        return False, "Close above head price between head and right neck"
    if float(right_head_closes.min()) < right_neck.price:
        return False, "Close below right neck price between head and right neck"
    if float(right_leg_closes.max()) > right_shoulder.price:
        return False, "Close above right shoulder price between right neck and right shoulder"
    if float(right_leg_closes.min()) < right_neck.price:
        return False, "Close below right neck price between right neck and right shoulder"
    return True, ""


def validate_shoulder_neck_bar_distances(points: list[PivotPoint]) -> tuple[bool, str]:
    left_shoulder, left_neck, _, right_neck, right_shoulder = points
    left_bars = left_neck.index - left_shoulder.index
    right_bars = right_shoulder.index - right_neck.index
    if left_bars <= 1 or right_bars <= 1:
        return False, (
            f"左肩到左颈、右颈到右肩的K线数量都必须大于1，"
            f"当前左侧 {left_bars} 根，右侧 {right_bars} 根"
        )
    return True, ""


def inverse_prior_high_exceeds_left_neck(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    lookback: int = 20,
) -> bool:
    start_index = max(0, left_shoulder.index - lookback)
    prior_highs = df.iloc[start_index:left_shoulder.index]["high"]
    return not prior_highs.empty and float(prior_highs.max()) > left_neck.price


def validate_head_shoulders_structure(points: list[PivotPoint], config: HeadShoulderTopConfig) -> tuple[bool, list[str], int]:
    if len(points) != 5:
        return False, ["关键点数量不是5个"], 0
    p1, p2, p3, p4, p5 = points
    if [p.kind for p in points] != ["high", "low", "high", "low", "high"]:
        return False, ["结构不是高-低-高-低-高"], 0

    bars_ok, bars_reason = validate_shoulder_neck_bar_distances(points)
    if not bars_ok:
        return False, [bars_reason], 0

    if p3.price < max(p1.price, p5.price):
        return False, ["头部低于左肩或右肩，头肩顶结构不成立"], 0

    if config.require_head_beyond_shoulders_and_necks and p3.price <= max(p1.price, p2.price, p4.price, p5.price):
        return False, ["头部必须高于左肩、右肩、左颈、右颈"], 0

    min_head_to_neck_height = config.min_head_to_neck_height if config.min_head_to_neck_height > 0 else min_head_to_neck_height_by_price(p3.price)
    left_head_to_neck_height = p3.price - p2.price
    right_head_to_neck_height = p3.price - p4.price
    if left_head_to_neck_height <= min_head_to_neck_height and right_head_to_neck_height <= min_head_to_neck_height:
        return False, [
            f"C到A1/A2高度不足：C-A1 {left_head_to_neck_height:.2f}，C-A2 {right_head_to_neck_height:.2f}，"
            f"要求至少一侧大于 {min_head_to_neck_height:.2f}"
        ], 0

    shoulder_diff = abs(p1.price - p5.price) / max(p1.price, p5.price)
    if shoulder_diff > config.max_shoulder_diff_pct:
        return False, [f"左右肩差异过大，当前 {shoulder_diff * 100:.2f}%"], 0

    neck_diff = abs(p2.price - p4.price) / max(p2.price, p4.price)
    if neck_diff > config.max_neck_diff_pct:
        return False, [f"两个颈线低点差异过大，当前 {neck_diff * 100:.2f}%"], 0

    if p5.price < p1.price * config.min_right_shoulder_ratio_to_left:
        return False, ["右肩过低，更像直接下跌，不像标准头肩顶"], 0

    if config.right_shoulder_must_below_head and p5.price >= p3.price:
        return False, ["右肩高于或等于头部，头肩顶结构不成立"], 0

    left_shoulder_to_neck_height = p1.price - p2.price
    left_neck_to_head_height = p3.price - p2.price
    if left_shoulder_to_neck_height <= 0 or left_neck_to_head_height <= 0:
        return False, ["左肩、左颈、头部高度关系不成立"], 0
    left_shoulder_height_ratio = left_shoulder_to_neck_height / left_neck_to_head_height
    right_shoulder_to_neck_height = p5.price - p4.price
    right_neck_to_head_height = p3.price - p4.price
    if right_shoulder_to_neck_height <= 0 or right_neck_to_head_height <= 0:
        return False, ["右肩、右颈、头部高度关系不成立"], 0
    if config.min_shoulder_to_neck_height > 0:
        if left_shoulder_to_neck_height < config.min_shoulder_to_neck_height:
            return False, [f"左颈到左肩价格差不足 {config.min_shoulder_to_neck_height:.2f}，当前 {left_shoulder_to_neck_height:.2f}"], 0
        if right_shoulder_to_neck_height < config.min_shoulder_to_neck_height:
            return False, [f"右颈到右肩价格差不足 {config.min_shoulder_to_neck_height:.2f}，当前 {right_shoulder_to_neck_height:.2f}"], 0
    if config.require_shoulders_between_opposite_neck_and_head:
        if not (p4.price <= p1.price <= p3.price):
            return False, ["左肩价格必须位于头部到右颈价格之间"], 0
        if not (p2.price <= p5.price <= p3.price):
            return False, ["右肩价格必须位于左颈到头部价格之间"], 0
    right_shoulder_height_ratio = right_shoulder_to_neck_height / right_neck_to_head_height
    if (
        left_shoulder_height_ratio < config.min_shoulder_to_head_height_ratio
        and right_shoulder_height_ratio < config.min_shoulder_to_head_height_ratio
    ):
        return False, [
            f"左右肩到颈线高度占颈线到头部高度均不足，左侧 {left_shoulder_height_ratio * 100:.2f}%，"
            f"右侧 {right_shoulder_height_ratio * 100:.2f}%，要求至少一侧达到 "
            f"{config.min_shoulder_to_head_height_ratio * 100:.2f}%"
        ], 0

    left_leg_bars = p2.index - p1.index
    right_leg_bars = p5.index - p4.index
    leg_ratio = right_leg_bars / left_leg_bars
    if config.enable_right_leg_ratio_filter and (
        leg_ratio < config.min_right_leg_to_left_leg_ratio
        or leg_ratio > config.max_right_leg_to_left_leg_ratio
    ):
        return False, [
            f"右颈到右肩K线数量不匹配，当前为左肩到左颈的 {leg_ratio:.2f} 倍，"
            f"要求 {config.min_right_leg_to_left_leg_ratio:.2f}-{config.max_right_leg_to_left_leg_ratio:.2f} 倍"
        ], 0

    left_neck_to_head_bars = max(1, p3.index - p2.index)
    head_to_right_neck_bars = max(1, p4.index - p3.index)


    return True, [
        "头部高于左右肩",
        f"左右肩高度接近，差异 {shoulder_diff * 100:.2f}%",
        f"两个颈线低点接近，差异 {neck_diff * 100:.2f}%",
        f"左肩高度占左颈到头部高度 {left_shoulder_height_ratio * 100:.2f}%",
        f"右肩高度占右颈到头部高度 {right_shoulder_height_ratio * 100:.2f}%",
        f"右颈到右肩K线数量为左肩到左颈的 {leg_ratio:.2f} 倍",
        f"左颈到头部 {left_neck_to_head_bars} 根K线，头部到右颈 {head_to_right_neck_bars} 根K线",
        "右肩没有过度走弱",
        "右肩低于头部",
    ], 0


def validate_inverse_head_shoulders_structure(
    points: list[PivotPoint],
    config: HeadShoulderTopConfig,
) -> tuple[bool, list[str], int]:
    if len(points) != 5:
        return False, ["关键点数量不是5个"], 0
    p1, p2, p3, p4, p5 = points
    if [p.kind for p in points] != ["low", "high", "low", "high", "low"]:
        return False, ["结构不是低-高-低-高-低"], 0

    bars_ok, bars_reason = validate_shoulder_neck_bar_distances(points)
    if not bars_ok:
        return False, [bars_reason], 0

    if p3.price > min(p1.price, p5.price):
        return False, ["头部高于左肩或右肩，反向头肩底结构不成立"], 0
    if config.require_head_beyond_shoulders_and_necks and p3.price >= min(p1.price, p2.price, p4.price, p5.price):
        return False, ["头部必须低于左肩、右肩、左颈、右颈"], 0

    min_head_to_neck_height = config.min_head_to_neck_height if config.min_head_to_neck_height > 0 else min_head_to_neck_height_by_price(p3.price)
    left_head_to_neck_height = p2.price - p3.price
    right_head_to_neck_height = p4.price - p3.price
    if left_head_to_neck_height <= min_head_to_neck_height and right_head_to_neck_height <= min_head_to_neck_height:
        return False, [
            f"C到A1/A2高度不足：A1-C {left_head_to_neck_height:.2f}，"
            f"A2-C {right_head_to_neck_height:.2f}，要求至少一侧大于 {min_head_to_neck_height:.2f}"
        ], 0

    shoulder_diff = abs(p1.price - p5.price) / max(p1.price, p5.price)
    if shoulder_diff > config.max_shoulder_diff_pct:
        return False, [f"左右肩差异过大，当前 {shoulder_diff * 100:.2f}%"], 0

    neck_diff = abs(p2.price - p4.price) / max(p2.price, p4.price)
    if neck_diff > config.max_neck_diff_pct:
        return False, [f"两个颈线高点差异过大，当前 {neck_diff * 100:.2f}%"], 0

    max_right_shoulder_price = p1.price * (2 - config.min_right_shoulder_ratio_to_left)
    if p5.price > max_right_shoulder_price:
        return False, ["右肩过高，不像标准反向头肩底"], 0

    if config.right_shoulder_must_below_head and p5.price <= p3.price:
        return False, ["右肩低于或等于头部，反向头肩底结构不成立"], 0

    left_shoulder_to_neck_height = p2.price - p1.price
    left_neck_to_head_height = p2.price - p3.price
    if left_shoulder_to_neck_height <= 0 or left_neck_to_head_height <= 0:
        return False, ["左肩、左颈、头部高度关系不成立"], 0
    left_shoulder_height_ratio = left_shoulder_to_neck_height / left_neck_to_head_height

    right_shoulder_to_neck_height = p4.price - p5.price
    right_neck_to_head_height = p4.price - p3.price
    if right_shoulder_to_neck_height <= 0 or right_neck_to_head_height <= 0:
        return False, ["右肩、右颈、头部高度关系不成立"], 0
    if config.min_shoulder_to_neck_height > 0:
        if left_shoulder_to_neck_height < config.min_shoulder_to_neck_height:
            return False, [f"左颈到左肩价格差不足 {config.min_shoulder_to_neck_height:.2f}，当前 {left_shoulder_to_neck_height:.2f}"], 0
        if right_shoulder_to_neck_height < config.min_shoulder_to_neck_height:
            return False, [f"右颈到右肩价格差不足 {config.min_shoulder_to_neck_height:.2f}，当前 {right_shoulder_to_neck_height:.2f}"], 0
    if config.require_shoulders_between_opposite_neck_and_head:
        if not (p3.price <= p1.price <= p4.price):
            return False, ["左肩价格必须位于头部到右颈价格之间"], 0
        if not (p3.price <= p5.price <= p2.price):
            return False, ["右肩价格必须位于头部到左颈价格之间"], 0
    right_shoulder_height_ratio = right_shoulder_to_neck_height / right_neck_to_head_height
    if (
        left_shoulder_height_ratio < config.min_shoulder_to_head_height_ratio
        and right_shoulder_height_ratio < config.min_shoulder_to_head_height_ratio
    ):
        return False, [
            f"左右肩到颈线高度占颈线到头部高度均不足，左侧 {left_shoulder_height_ratio * 100:.2f}%，"
            f"右侧 {right_shoulder_height_ratio * 100:.2f}%，要求至少一侧达到 "
            f"{config.min_shoulder_to_head_height_ratio * 100:.2f}%"
        ], 0

    left_leg_bars = p2.index - p1.index
    right_leg_bars = p5.index - p4.index
    leg_ratio = right_leg_bars / left_leg_bars
    if config.enable_right_leg_ratio_filter and (
        leg_ratio < config.min_right_leg_to_left_leg_ratio
        or leg_ratio > config.max_right_leg_to_left_leg_ratio
    ):
        return False, [
            f"右颈到右肩K线数量不匹配，当前为左肩到左颈的 {leg_ratio:.2f} 倍，"
            f"要求 {config.min_right_leg_to_left_leg_ratio:.2f}-{config.max_right_leg_to_left_leg_ratio:.2f} 倍"
        ], 0

    left_neck_to_head_bars = max(1, p3.index - p2.index)
    head_to_right_neck_bars = max(1, p4.index - p3.index)


    return True, [
        "头部低于左右肩",
        f"左右肩高度接近，差异 {shoulder_diff * 100:.2f}%",
        f"两个颈线高点接近，差异 {neck_diff * 100:.2f}%",
        f"左肩高度占左颈到头部高度 {left_shoulder_height_ratio * 100:.2f}%",
        f"右肩高度占右颈到头部高度 {right_shoulder_height_ratio * 100:.2f}%",
        f"右颈到右肩K线数量为左肩到左颈的 {leg_ratio:.2f} 倍",
        f"左颈到头部 {left_neck_to_head_bars} 根K线，头部到右颈 {head_to_right_neck_bars} 根K线",
        "右肩没有反弹过高",
        "右肩高于头部",
    ], 0


def check_ma_bearish_filter(df: pd.DataFrame, index: int, config: HeadShoulderTopConfig) -> tuple[bool, str, int]:
    if not config.enable_ma_filter:
        return True, "未启用均线过滤", 0
    score, reasons = calculate_ma_trend_score(df, index, bullish=False)
    if score == 0:
        return False, "; ".join(reasons), 0
    return True, "; ".join(reasons), score

    if not config.enable_ma_filter:
        return True, "未启用均线过滤", 0
    row = df.loc[index]
    ma_short = row.get(f"ma{config.ma_short}")
    ma_mid = row.get(f"ma{config.ma_mid}")
    ma_long = row.get(f"ma{config.ma_long}")
    if pd.isna(ma_short) or pd.isna(ma_mid) or pd.isna(ma_long):
        return False, "均线数据不足", 0
    score = 0
    reasons: list[str] = []
    if config.require_ma_bearish_alignment:
        if ma_short < ma_mid < ma_long:
            score += 10
            reasons.append(f"均线空头排列：MA{config.ma_short} < MA{config.ma_mid} < MA{config.ma_long}")
        else:
            return False, "均线未形成空头排列", 0
    if config.require_close_below_ma_long:
        if row["close"] < ma_long:
            score += 10
            reasons.append(f"收盘价在 MA{config.ma_long} 下方")
        else:
            return False, f"收盘价未跌破 MA{config.ma_long}", 0
    return True, "；".join(reasons) or "均线过滤通过", score


def check_ma_bullish_filter(df: pd.DataFrame, index: int, config: HeadShoulderTopConfig) -> tuple[bool, str, int]:
    if not config.enable_ma_filter:
        return True, "未启用均线过滤", 0
    score, reasons = calculate_ma_trend_score(df, index, bullish=True)
    if score == 0:
        return False, "; ".join(reasons), 0
    return True, "; ".join(reasons), score

    if not config.enable_ma_filter:
        return True, "未启用均线过滤", 0
    row = df.loc[index]
    ma_short = row.get(f"ma{config.ma_short}")
    ma_mid = row.get(f"ma{config.ma_mid}")
    ma_long = row.get(f"ma{config.ma_long}")
    if pd.isna(ma_short) or pd.isna(ma_mid) or pd.isna(ma_long):
        return False, "均线数据不足", 0
    score = 0
    reasons: list[str] = []
    if config.require_ma_bearish_alignment:
        if ma_short > ma_mid > ma_long:
            score += 10
            reasons.append(f"均线多头排列：MA{config.ma_short} > MA{config.ma_mid} > MA{config.ma_long}")
        else:
            return False, "均线未形成多头排列", 0
    if config.require_close_below_ma_long:
        if row["close"] > ma_long:
            score += 10
            reasons.append(f"收盘价在 MA{config.ma_long} 上方")
        else:
            return False, f"收盘价未突破 MA{config.ma_long}", 0
    return True, "；".join(reasons) or "均线过滤通过", score


def check_macd_top_divergence(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    head: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, str, int]:
    if not config.enable_macd_divergence:
        return True, "未启用 MACD 顶背离过滤", 0
    if head.price <= left_shoulder.price * (1 + config.macd_price_new_high_pct):
        return False, "价格没有形成明显新高，不满足 MACD 顶背离前提", 0
    macd_col = "macd_hist" if config.use_macd_hist_for_divergence else "macd_dif"
    macd_name = "MACD柱" if config.use_macd_hist_for_divergence else "DIF"
    left_macd = df.loc[left_shoulder.index, macd_col]
    head_macd = df.loc[head.index, macd_col]
    if pd.isna(left_macd) or pd.isna(head_macd):
        return False, "MACD 数据不足", 0
    if head_macd < left_macd:
        return True, f"出现 MACD 顶背离：头部价格创新高，但{macd_name}降低", 15
    return False, f"未出现 MACD 顶背离：头部{macd_name}未降低", 0


def check_macd_bottom_divergence(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    head: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, str, int]:
    if not config.enable_macd_divergence:
        return True, "未启用 MACD 底背离过滤", 0
    if head.price >= left_shoulder.price * (1 - config.macd_price_new_high_pct):
        return False, "价格没有形成明显新低，不满足 MACD 底背离前提", 0
    macd_col = "macd_hist" if config.use_macd_hist_for_divergence else "macd_dif"
    macd_name = "MACD柱" if config.use_macd_hist_for_divergence else "DIF"
    left_macd = df.loc[left_shoulder.index, macd_col]
    head_macd = df.loc[head.index, macd_col]
    if pd.isna(left_macd) or pd.isna(head_macd):
        return False, "MACD 数据不足", 0
    if head_macd > left_macd:
        return True, f"出现 MACD 底背离：头部价格创新低，但{macd_name}抬高", 15
    return False, f"未出现 MACD 底背离：头部{macd_name}未抬高", 0


def check_neckline_break(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, int | None, pd.Timestamp | None, float | None, str, int]:
    start_index = right_shoulder.index + 1
    end_index = min(len(df) - 1, right_shoulder.index + config.max_bars_after_right_shoulder)
    if start_index >= len(df):
        return False, None, None, None, "右肩之后没有足够K线", 0
    break_col = "low" if config.break_by == "low" else "close"
    for i in range(start_index, end_index + 1):
        neckline_price = calculate_neckline_price(left_neck, right_neck, i)
        break_price = float(df.loc[i, break_col])
        if break_price >= neckline_price * (1 - config.neckline_break_pct):
            continue
        return True, i, df.loc[i, "datetime"], break_price, (
            f"跌破颈线确认，跌破价 {break_price:.2f}，颈线价 {neckline_price:.2f}"
        ), 15
    return False, None, None, None, "尚未有效跌破颈线", 0


def check_neckline_break_up(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, int | None, pd.Timestamp | None, float | None, str, int]:
    start_index = right_shoulder.index + 1
    end_index = min(len(df) - 1, right_shoulder.index + config.max_bars_after_right_shoulder)
    if start_index >= len(df):
        return False, None, None, None, "右肩之后没有足够K线", 0
    break_col = "high" if config.break_by == "low" else "close"
    for i in range(start_index, end_index + 1):
        neckline_price = calculate_neckline_price(left_neck, right_neck, i)
        break_price = float(df.loc[i, break_col])
        if break_price <= neckline_price * (1 + config.neckline_break_pct):
            continue
        return True, i, df.loc[i, "datetime"], break_price, (
            f"突破颈线确认，突破价 {break_price:.2f}，颈线价 {neckline_price:.2f}"
        ), 15
    return False, None, None, None, "尚未有效突破颈线", 0


def check_right_shoulder_midpoint_trigger(
    df: pd.DataFrame,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
    *,
    inverse: bool,
) -> tuple[bool, int | None, pd.Timestamp | None, float | None, float]:
    start_index = right_shoulder.index + 1
    end_index = min(len(df) - 1, right_shoulder.index + config.max_bars_after_right_shoulder)
    midpoint_price = (right_neck.price + right_shoulder.price) / 2
    if start_index >= len(df):
        return False, None, None, None, midpoint_price

    for i in range(start_index, end_index + 1):
        close_price = float(df.loc[i, "close"])
        reached = close_price >= midpoint_price if inverse else close_price <= midpoint_price
        if reached:
            return True, i, df.loc[i, "datetime"], close_price, midpoint_price

        invalidated = (
            close_price < right_shoulder.price
            if inverse
            else close_price > right_shoulder.price
        )
        if invalidated:
            return False, None, None, None, midpoint_price
    return False, None, None, None, midpoint_price


def check_neckline_break_then_pullback(
    df: pd.DataFrame,
    left_neck: PivotPoint,
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    *,
    inverse: bool,
    lookahead_bars: int = PULLBACK_LOOKAHEAD_BARS,
) -> tuple[bool, int | None, pd.Timestamp | None, float | None, int | None, pd.Timestamp | None, float | None, float]:
    start_index = right_shoulder.index + 1
    end_index = min(len(df) - 1, right_shoulder.index + lookahead_bars)
    pullback_price = (
        right_shoulder.price + (right_neck.price - right_shoulder.price) / 4
        if inverse
        else right_shoulder.price - (right_shoulder.price - right_neck.price) / 4
    )
    if start_index >= len(df):
        return False, None, None, None, None, None, None, pullback_price

    neck_boundary_price = max(left_neck.price, right_neck.price) if inverse else min(left_neck.price, right_neck.price)
    break_index: int | None = None
    break_time: pd.Timestamp | None = None
    break_price: float | None = None
    for i in range(start_index, end_index + 1):
        neckline_price = calculate_neckline_price(left_neck, right_neck, i)
        if break_index is None:
            candidate_break_price = float(df.loc[i, "high" if inverse else "low"])
            broke_neckline = candidate_break_price > neckline_price if inverse else candidate_break_price < neckline_price
            broke_neck_boundary = candidate_break_price > neck_boundary_price if inverse else candidate_break_price < neck_boundary_price
            if not (broke_neckline and broke_neck_boundary):
                continue
            break_index = i
            break_time = df.loc[i, "datetime"]
            break_price = candidate_break_price
            continue

        candidate_retest_price = float(df.loc[i, "low" if inverse else "high"])
        retested = candidate_retest_price <= pullback_price if inverse else candidate_retest_price >= pullback_price
        if retested:
            return True, break_index, break_time, break_price, i, df.loc[i, "datetime"], candidate_retest_price, pullback_price

    return False, break_index, break_time, break_price, None, None, None, pullback_price


def should_emit_pullback_alert(total_score: int, pattern_result: dict[str, Any]) -> bool:
    return (
        total_score <= PULLBACK_MAX_TREND_SCORE
        and int(pattern_result["final_score"]) >= PULLBACK_MIN_PATTERN_SCORE
    )


def scan_head_shoulders_top(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
    hourly_df: pd.DataFrame | None = None,
    daily_df: pd.DataFrame | None = None,
) -> list[HeadShoulderTopSignal]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_structure_pivots_for_timeframe(df, timeframe, config)
    signals: list[HeadShoulderTopSignal] = []

    for p1, p2, p3, p4, p5 in iter_timeframe_pattern_candidates(
        df,
        timeframe,
        config,
        ["high", "low", "high", "low", "high"],
        structure_pivots=pivots,
    ):
        if not passes_head_neck_bar_limit(timeframe, p2, p3, p4):
            continue
        ok, reasons, total_score = validate_head_shoulders_structure([p1, p2, p3, p4, p5], config)
        if not ok:
            continue
        closes_ok, _ = validate_candle_close_constraints(
            df,
            [p1, p2, p3, p4, p5],
            inverse=False,
        )
        if not closes_ok:
            continue
        trend_score, trend_reasons = calculate_combined_trend_score(
            hourly_df,
            bullish=False,
            signal_time=p5.time,
            daily_df=daily_df,
            config=config,
            head_time=p3.time,
        )
        total_score += trend_score
        reasons.extend(trend_reasons)

        triggered, trigger_index, trigger_time, trigger_price, midpoint_price = check_right_shoulder_midpoint_trigger(
            df,
            p4,
            p5,
            config,
            inverse=False,
        )
        if not triggered:
            continue
        if trigger_index is None or trigger_price is None:
            continue

        neckline_price = calculate_neckline_price(p2, p4, p5.index)
        qtr = calculate_qtr(df, p2, p4)
        pattern_result = calculate_pattern_score(
            df,
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            inverse=False,
            qtr=qtr,
            trigger_index=trigger_index,
            trigger_price=trigger_price,
            midpoint=midpoint_price,
        )
        if not _pattern_quality_allows_alert(pattern_result, config):
            continue

        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="head_shoulders_top",
            alert_type="right_shoulder_confirmed",
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            neckline_price=neckline_price,
            confirmed=False,
            score=total_score,
            qtr=qtr,
            trend_label=trend_label_from_score(total_score, bullish=False),
            reasons=reasons + [
                f"右肩形成后价格回落至右颈与右肩半程价 {midpoint_price:.2f}"
            ],
            retest_time=trigger_time,
            retest_price=trigger_price,
            pattern_score=pattern_result["final_score"],
            pattern_raw_score=pattern_result["raw_score"],
            pattern_grade=pattern_result["grade"],
            pattern_caps=pattern_result["caps"],
            pattern_sections=pattern_result["sections"],
            pattern_metrics=pattern_result["metrics"],
            message=(
                f"{symbol} {timeframe} 头肩顶右肩半程触发，当前评分 {total_score}。"
                f"触发价 {trigger_price:.2f}，半程价 {midpoint_price:.2f}。"
            ),
        ))

        if should_emit_pullback_alert(total_score, pattern_result):
            (
                pullback_triggered,
                break_index,
                break_time,
                break_price,
                pullback_index,
                pullback_time,
                pullback_price,
                pullback_level,
            ) = check_neckline_break_then_pullback(
                df,
                p2,
                p4,
                p5,
                inverse=False,
            )
            if pullback_triggered and break_time is not None and break_price is not None and pullback_time is not None and pullback_price is not None:
                neckline_at_break = calculate_neckline_price(p2, p4, break_index or p5.index)
                signals.append(HeadShoulderTopSignal(
                    symbol=symbol,
                    timeframe=timeframe,
                    pattern="head_shoulders_top",
                    alert_type="head_shoulders_top_pullback",
                    left_shoulder=p1,
                    left_neck=p2,
                    head=p3,
                    right_neck=p4,
                    right_shoulder=p5,
                    neckline_price=neckline_at_break,
                    confirmed=True,
                    score=total_score,
                    qtr=qtr,
                    trend_label=trend_label_from_score(total_score, bullish=False),
                    reasons=reasons + [
                        f"趋势评分 {total_score} <= {PULLBACK_MAX_TREND_SCORE}",
                        f"形态质量评分 {pattern_result['final_score']} >= {PULLBACK_MIN_PATTERN_SCORE}",
                        f"右肩后 {PULLBACK_LOOKAHEAD_BARS} 条K线内先跌破颈线，再涨回四分之一反抽位 {pullback_level:.2f}",
                    ],
                    break_time=break_time,
                    break_price=break_price,
                    retest_time=pullback_time,
                    retest_price=pullback_price,
                    pattern_score=pattern_result["final_score"],
                    pattern_raw_score=pattern_result["raw_score"],
                    pattern_grade=pattern_result["grade"],
                    pattern_caps=pattern_result["caps"],
                    pattern_sections=pattern_result["sections"],
                    pattern_metrics=pattern_result["metrics"],
                    message=(
                        f"{symbol} {timeframe} 头肩顶反抽，趋势评分 {total_score}，"
                        f"形态评分 {pattern_result['final_score']}。跌破价 {break_price:.2f}，"
                        f"反抽价 {pullback_price:.2f}，反抽位 {pullback_level:.2f}。"
                    ),
                ))

        continue

        confirmed, break_index, break_time, break_price, reason, score = check_neckline_break(df, p2, p4, p5, config)
        if not confirmed:
            continue

        total_score += score
        reasons.append(reason)
        assert break_index is not None

        if config.enable_score and total_score < config.min_score_to_alert:
            continue

        neckline_price = calculate_neckline_price(p2, p4, break_index)
        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="head_shoulders_top",
            alert_type="neckline_break",
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            neckline_price=neckline_price,
            confirmed=True,
            score=total_score,
            trend_label=trend_label_from_score(total_score, bullish=False),
            reasons=reasons,
            break_time=break_time,
            break_price=break_price,
            message=(
                f"{symbol} {timeframe} 头肩顶确认，评分 {total_score}。"
                f"跌破价格 {break_price:.2f}，颈线价 {neckline_price:.2f}。"
            ),
        ))

    return signals


def scan_inverse_head_shoulders(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
    hourly_df: pd.DataFrame | None = None,
    daily_df: pd.DataFrame | None = None,
) -> list[HeadShoulderTopSignal]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_structure_pivots_for_timeframe(df, timeframe, config)
    signals: list[HeadShoulderTopSignal] = []

    for p1, p2, p3, p4, p5 in iter_timeframe_pattern_candidates(
        df,
        timeframe,
        config,
        ["low", "high", "low", "high", "low"],
        structure_pivots=pivots,
    ):
        if not passes_head_neck_bar_limit(timeframe, p2, p3, p4):
            continue
        ok, reasons, total_score = validate_inverse_head_shoulders_structure([p1, p2, p3, p4, p5], config)
        if not ok:
            continue
        if (
            timeframe in INVERSE_PRIOR_HIGH_TIMEFRAMES
            and not inverse_prior_high_exceeds_left_neck(df, p1, p2)
        ):
            continue
        closes_ok, _ = validate_candle_close_constraints(
            df,
            [p1, p2, p3, p4, p5],
            inverse=True,
        )
        if not closes_ok:
            continue
        trend_score, trend_reasons = calculate_combined_trend_score(
            hourly_df,
            bullish=True,
            signal_time=p5.time,
            daily_df=daily_df,
            config=config,
            head_time=p3.time,
        )
        total_score += trend_score
        reasons.extend(trend_reasons)

        triggered, trigger_index, trigger_time, trigger_price, midpoint_price = check_right_shoulder_midpoint_trigger(
            df,
            p4,
            p5,
            config,
            inverse=True,
        )
        if not triggered:
            continue
        if trigger_index is None or trigger_price is None:
            continue

        neckline_price = calculate_neckline_price(p2, p4, p5.index)
        qtr = calculate_qtr(df, p2, p4)
        pattern_result = calculate_pattern_score(
            df,
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            inverse=True,
            qtr=qtr,
            trigger_index=trigger_index,
            trigger_price=trigger_price,
            midpoint=midpoint_price,
        )
        if not _pattern_quality_allows_alert(pattern_result, config):
            continue

        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="inverse_head_shoulders",
            alert_type="right_shoulder_confirmed",
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            neckline_price=neckline_price,
            confirmed=False,
            score=total_score,
            qtr=qtr,
            trend_label=trend_label_from_score(total_score, bullish=True),
            reasons=reasons + [
                f"右肩形成后价格上升至右颈与右肩半程价 {midpoint_price:.2f}"
            ],
            retest_time=trigger_time,
            retest_price=trigger_price,
            pattern_score=pattern_result["final_score"],
            pattern_raw_score=pattern_result["raw_score"],
            pattern_grade=pattern_result["grade"],
            pattern_caps=pattern_result["caps"],
            pattern_sections=pattern_result["sections"],
            pattern_metrics=pattern_result["metrics"],
            message=(
                f"{symbol} {timeframe} 反向头肩右肩半程触发，评分 {total_score}。"
                f"触发价 {trigger_price:.2f}，半程价 {midpoint_price:.2f}。"
            ),
        ))

        if should_emit_pullback_alert(total_score, pattern_result):
            (
                pullback_triggered,
                break_index,
                break_time,
                break_price,
                pullback_index,
                pullback_time,
                pullback_price,
                pullback_level,
            ) = check_neckline_break_then_pullback(
                df,
                p2,
                p4,
                p5,
                inverse=True,
            )
            if pullback_triggered and break_time is not None and break_price is not None and pullback_time is not None and pullback_price is not None:
                neckline_at_break = calculate_neckline_price(p2, p4, break_index or p5.index)
                signals.append(HeadShoulderTopSignal(
                    symbol=symbol,
                    timeframe=timeframe,
                    pattern="inverse_head_shoulders",
                    alert_type="inverse_head_shoulders_pullback",
                    left_shoulder=p1,
                    left_neck=p2,
                    head=p3,
                    right_neck=p4,
                    right_shoulder=p5,
                    neckline_price=neckline_at_break,
                    confirmed=True,
                    score=total_score,
                    qtr=qtr,
                    trend_label=trend_label_from_score(total_score, bullish=True),
                    reasons=reasons + [
                        f"趋势评分 {total_score} <= {PULLBACK_MAX_TREND_SCORE}",
                        f"形态质量评分 {pattern_result['final_score']} >= {PULLBACK_MIN_PATTERN_SCORE}",
                        f"右肩后 {PULLBACK_LOOKAHEAD_BARS} 条K线内先突破颈线，再跌回四分之一反抽位 {pullback_level:.2f}",
                    ],
                    break_time=break_time,
                    break_price=break_price,
                    retest_time=pullback_time,
                    retest_price=pullback_price,
                    pattern_score=pattern_result["final_score"],
                    pattern_raw_score=pattern_result["raw_score"],
                    pattern_grade=pattern_result["grade"],
                    pattern_caps=pattern_result["caps"],
                    pattern_sections=pattern_result["sections"],
                    pattern_metrics=pattern_result["metrics"],
                    message=(
                        f"{symbol} {timeframe} 反向头肩顶反抽，趋势评分 {total_score}，"
                        f"形态评分 {pattern_result['final_score']}。突破价 {break_price:.2f}，"
                        f"反抽价 {pullback_price:.2f}，反抽位 {pullback_level:.2f}。"
                    ),
                ))
        continue

        confirmed, break_index, break_time, break_price, reason, score = check_neckline_break_up(df, p2, p4, p5, config)
        if not confirmed:
            neckline_price = calculate_neckline_price(p2, p4, p5.index)
            signals.append(HeadShoulderTopSignal(
                symbol=symbol,
                timeframe=timeframe,
                pattern="inverse_head_shoulders",
                left_shoulder=p1,
                left_neck=p2,
                head=p3,
                right_neck=p4,
                right_shoulder=p5,
                neckline_price=neckline_price,
                confirmed=False,
                score=total_score,
                trend_label=trend_label_from_score(total_score, bullish=True),
                reasons=reasons + [reason],
                message=f"{symbol} {timeframe} 疑似反向头肩顶，等待突破颈线确认，当前评分 {total_score}",
            ))
            continue

        total_score += score
        reasons.append(reason)
        assert break_index is not None

        if config.enable_score and total_score < config.min_score_to_alert:
            continue

        neckline_price = calculate_neckline_price(p2, p4, break_index)
        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="inverse_head_shoulders",
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            neckline_price=neckline_price,
            confirmed=True,
            score=total_score,
            trend_label=trend_label_from_score(total_score, bullish=True),
            reasons=reasons,
            break_time=break_time,
            break_price=break_price,
            message=(
                f"{symbol} {timeframe} 反向头肩顶确认，评分 {total_score}。"
                f"突破价格 {break_price:.2f}，颈线价 {neckline_price:.2f}。"
            ),
        ))

    return signals


def signal_end_index(signal: HeadShoulderTopSignal) -> int:
    if signal.break_time is None:
        return signal.right_shoulder.index
    return max(signal.right_shoulder.index, signal.right_neck.index)


def signal_range(signal: HeadShoulderTopSignal) -> tuple[int, int]:
    return signal.left_shoulder.index, signal_end_index(signal)


def signal_overlap_ratio(first: HeadShoulderTopSignal, second: HeadShoulderTopSignal) -> float:
    first_start, first_end = signal_range(first)
    second_start, second_end = signal_range(second)
    overlap_start = max(first_start, second_start)
    overlap_end = min(first_end, second_end)
    if overlap_end < overlap_start:
        return 0.0
    overlap = overlap_end - overlap_start + 1
    shorter = min(first_end - first_start + 1, second_end - second_start + 1)
    return overlap / max(1, shorter)


def signals_conflict(first: HeadShoulderTopSignal, second: HeadShoulderTopSignal) -> bool:
    if first.pattern != second.pattern:
        return False
    if first.alert_type != second.alert_type:
        return False
    if signals_share_head(first, second) and not signals_share_all_pivots(first, second):
        return False
    if signal_overlap_ratio(first, second) >= 0.7:
        return True
    return (
        first.pattern == second.pattern
        and first.confirmed
        and second.confirmed
        and first.break_time is not None
        and first.break_time == second.break_time
    )


def signals_share_head(first: HeadShoulderTopSignal, second: HeadShoulderTopSignal) -> bool:
    return (
        first.symbol == second.symbol
        and first.timeframe == second.timeframe
        and first.pattern == second.pattern
        and first.alert_type == second.alert_type
        and first.head.index == second.head.index
    )


def signals_share_all_pivots(first: HeadShoulderTopSignal, second: HeadShoulderTopSignal) -> bool:
    return (
        first.left_shoulder.index == second.left_shoulder.index
        and first.left_neck.index == second.left_neck.index
        and first.head.index == second.head.index
        and first.right_neck.index == second.right_neck.index
        and first.right_shoulder.index == second.right_shoulder.index
    )


def deduplicate_overlapping_signals(signals: list[HeadShoulderTopSignal]) -> list[HeadShoulderTopSignal]:
    def shoulder_diff(signal: HeadShoulderTopSignal) -> float:
        return abs(signal.left_shoulder.price - signal.right_shoulder.price) / max(signal.left_shoulder.price, signal.right_shoulder.price)

    def neck_diff(signal: HeadShoulderTopSignal) -> float:
        return abs(signal.left_neck.price - signal.right_neck.price) / max(signal.left_neck.price, signal.right_neck.price)

    def relative_span_diff(left_span: int, right_span: int) -> float:
        return abs(left_span - right_span) / max(1, left_span, right_span)

    def time_symmetry(signal: HeadShoulderTopSignal) -> tuple[float, float, float]:
        shoulder_span_diff = relative_span_diff(
            signal.head.index - signal.left_shoulder.index,
            signal.right_shoulder.index - signal.head.index,
        )
        neck_span_diff = relative_span_diff(
            signal.head.index - signal.left_neck.index,
            signal.right_neck.index - signal.head.index,
        )
        return (
            shoulder_span_diff + neck_span_diff,
            max(shoulder_span_diff, neck_span_diff),
            shoulder_span_diff,
        )

    def head_depth(signal: HeadShoulderTopSignal) -> float:
        neckline_at_head = calculate_neckline_price(
            signal.left_neck,
            signal.right_neck,
            signal.head.index,
        )
        depth = (
            signal.head.price - neckline_at_head
            if signal.pattern == "head_shoulders_top"
            else neckline_at_head - signal.head.price
        )
        return depth / max(abs(neckline_at_head), 1.0)

    def neckline_height(signal: HeadShoulderTopSignal) -> float:
        average_neckline = (signal.left_neck.price + signal.right_neck.price) / 2
        return average_neckline if signal.pattern == "inverse_head_shoulders" else -average_neckline

    def signal_rank(signal: HeadShoulderTopSignal) -> tuple[Any, ...]:
        return (
            signal.confirmed,
            *(-value for value in time_symmetry(signal)),
            head_depth(signal),
            neckline_height(signal),
            -shoulder_diff(signal),
            -neck_diff(signal),
            signal.score,
            signal.break_time or signal.right_shoulder.time,
        )

    ranked = sorted(
        signals,
        key=signal_rank,
        reverse=True,
    )
    selected: list[HeadShoulderTopSignal] = []
    for signal in ranked:
        if any(signals_conflict(signal, existing) for existing in selected):
            continue
        selected.append(signal)
    selected.sort(key=lambda signal: signal.break_time or signal.right_shoulder.time)
    return selected


def scan_head_shoulders(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
    hourly_df: pd.DataFrame | None = None,
    daily_df: pd.DataFrame | None = None,
) -> list[HeadShoulderTopSignal]:
    signals = scan_head_shoulders_top(df, symbol=symbol, timeframe=timeframe, config=config, hourly_df=hourly_df, daily_df=daily_df)
    signals.extend(scan_inverse_head_shoulders(df, symbol=symbol, timeframe=timeframe, config=config, hourly_df=hourly_df, daily_df=daily_df))
    if config.max_signal_age_bars > 0:
        min_right_shoulder_index = max(0, len(df) - config.max_signal_age_bars)
        signals = [
            signal for signal in signals
            if signal.right_shoulder.index >= min_right_shoulder_index
        ]
    signals = deduplicate_overlapping_signals(signals)
    signals.sort(key=lambda signal: signal.break_time or signal.right_shoulder.time)
    return signals


def prepare_chart_payload(
    df: pd.DataFrame,
    pivots: list[PivotPoint],
    signals: list[HeadShoulderTopSignal],
    config: HeadShoulderTopConfig,
) -> dict[str, Any]:
    ma_columns = [f"ma{period}" for period in config.ma_periods if f"ma{period}" in df.columns]

    def optional_float(value: Any) -> float | None:
        return None if pd.isna(value) else float(value)

    candles = [
        {
            "index": int(i),
            "time": row["datetime"].isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "ma": {column: optional_float(row[column]) for column in ma_columns},
        }
        for i, row in df.reset_index(drop=True).iterrows()
    ]
    neckline_segments = []
    for signal in signals:
        end_index = signal.right_shoulder.index if not signal.confirmed else next(
            (i for i, candle in enumerate(candles) if candle["time"] == signal.break_time.isoformat()),
            signal.right_shoulder.index,
        )
        neckline_segments.append({
            "from_index": signal.left_neck.index,
            "to_index": end_index,
            "from_price": signal.left_neck.price,
            "to_price": calculate_neckline_price(signal.left_neck, signal.right_neck, end_index),
            "confirmed": signal.confirmed,
        })
    return {
        "candles": candles,
        "pivots": [pivot.to_dict() for pivot in pivots],
        "necklines": neckline_segments,
    }
