from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


MAX_HEAD_NECK_BARS_BY_TIMEFRAME = {
    "1m": 60,
    "3m": 60,
    "5m": 60,
}
MIXED_PIVOT_CONFIRMATION_TIMEFRAMES = {"1m", "3m", "5m"}
STRUCTURE_PIVOT_WINDOW = 5
RIGHT_SHOULDER_PIVOT_WINDOW = 3


@dataclass
class HeadShoulderTopConfig:
    pivot_left: int = 3
    pivot_right: int = 3
    min_shoulder_to_head_height_ratio: float = 0.3
    max_shoulder_diff_pct: float = 0.004
    max_neck_diff_pct: float = 0.004
    min_right_leg_to_left_leg_ratio: float = 0.6
    max_right_leg_to_left_leg_ratio: float = 2.0
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
    trend_label: str = ""
    break_time: pd.Timestamp | None = None
    break_price: float | None = None
    retest_time: pd.Timestamp | None = None
    retest_price: float | None = None
    alert_type: str = "right_shoulder_confirmed"
    message: str = ""

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
            "break_time": self.break_time.isoformat() if self.break_time is not None else None,
            "break_price": self.break_price,
            "retest_time": self.retest_time.isoformat() if self.retest_time is not None else None,
            "retest_price": self.retest_price,
            "message": self.message,
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


def calculate_combined_trend_score(
    hourly_df: pd.DataFrame | None,
    bullish: bool,
    signal_time: pd.Timestamp | None = None,
    daily_df: pd.DataFrame | None = None,
) -> tuple[int, list[str]]:
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
    head_close = float(df.iloc[head.index]["close"])
    left_leg_closes = df.iloc[left_shoulder.index : left_neck.index + 1]["close"]
    left_head_closes = df.iloc[left_neck.index : head.index + 1]["close"]
    right_head_closes = df.iloc[head.index : right_neck.index + 1]["close"]
    right_leg_closes = df.iloc[right_neck.index : right_shoulder.index + 1]["close"]

    if inverse:
        if head_close >= min(left_shoulder.price, right_shoulder.price):
            return False, "Head close must be below both shoulder lows"
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

    if head_close <= max(left_shoulder.price, right_shoulder.price):
        return False, "Head close must be above both shoulder highs"
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


def validate_head_shoulders_structure(points: list[PivotPoint], config: HeadShoulderTopConfig) -> tuple[bool, list[str], int]:
    if len(points) != 5:
        return False, ["关键点数量不是5个"], 0
    p1, p2, p3, p4, p5 = points
    if [p.kind for p in points] != ["high", "low", "high", "low", "high"]:
        return False, ["结构不是高-低-高-低-高"], 0

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

    left_leg_bars = max(1, p2.index - p1.index)
    right_leg_bars = max(1, p5.index - p4.index)
    leg_ratio = right_leg_bars / left_leg_bars
    if leg_ratio < config.min_right_leg_to_left_leg_ratio or leg_ratio > config.max_right_leg_to_left_leg_ratio:
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

    left_leg_bars = max(1, p2.index - p1.index)
    right_leg_bars = max(1, p5.index - p4.index)
    leg_ratio = right_leg_bars / left_leg_bars
    if leg_ratio < config.min_right_leg_to_left_leg_ratio or leg_ratio > config.max_right_leg_to_left_leg_ratio:
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
    used_right_shoulder_setups: set[tuple[int, int, int, int]] = set()

    for p1, p2, p3, p4, p5 in iter_timeframe_pattern_candidates(
        df,
        timeframe,
        config,
        ["high", "low", "high", "low", "high"],
        structure_pivots=pivots,
    ):
        setup_key = (p1.index, p2.index, p3.index, p4.index)
        if setup_key in used_right_shoulder_setups:
            continue
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
        )
        total_score += trend_score
        reasons.extend(trend_reasons)
        used_right_shoulder_setups.add(setup_key)

        neckline_price = calculate_neckline_price(p2, p4, p5.index)
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
            trend_label=trend_label_from_score(total_score, bullish=False),
            reasons=reasons + ["右肩已确认，等待跌破颈线确认"],
            message=(
                f"{symbol} {timeframe} 头肩顶右肩确认，当前评分 {total_score}。"
                f"右肩价 {p5.price:.2f}，颈线价 {neckline_price:.2f}。"
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
        )
        total_score += trend_score
        reasons.extend(trend_reasons)

        neckline_price = calculate_neckline_price(p2, p4, p5.index)
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
            trend_label=trend_label_from_score(total_score, bullish=True),
            reasons=reasons,
            message=f"{symbol} {timeframe} 反向头肩底右肩确认，评分 {total_score}",
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
    if signal_overlap_ratio(first, second) >= 0.7:
        return True
    return (
        first.pattern == second.pattern
        and first.confirmed
        and second.confirmed
        and first.break_time is not None
        and first.break_time == second.break_time
    )


def deduplicate_overlapping_signals(signals: list[HeadShoulderTopSignal]) -> list[HeadShoulderTopSignal]:
    def shoulder_diff(signal: HeadShoulderTopSignal) -> float:
        return abs(signal.left_shoulder.price - signal.right_shoulder.price) / max(signal.left_shoulder.price, signal.right_shoulder.price)

    def neck_diff(signal: HeadShoulderTopSignal) -> float:
        return abs(signal.left_neck.price - signal.right_neck.price) / max(signal.left_neck.price, signal.right_neck.price)

    def left_setup_distance(signal: HeadShoulderTopSignal) -> int:
        return signal.head.index - signal.left_shoulder.index

    def left_neck_distance(signal: HeadShoulderTopSignal) -> int:
        return signal.head.index - signal.left_neck.index

    def inverse_neck_priority(signal: HeadShoulderTopSignal) -> tuple[float, float]:
        if signal.pattern != "inverse_head_shoulders":
            return (0.0, 0.0)
        return (
            signal.right_neck.price,
            -abs(signal.left_neck.price - signal.right_neck.price),
        )

    ranked = sorted(
        signals,
        key=lambda signal: (
            signal.confirmed,
            signal.head.price if signal.pattern == "head_shoulders_top" else -signal.head.price,
            *inverse_neck_priority(signal),
            -left_neck_distance(signal) if signal.pattern == "head_shoulders_top" else left_neck_distance(signal),
            -left_setup_distance(signal) if signal.pattern == "head_shoulders_top" else left_setup_distance(signal),
            signal.right_neck.index,
            signal.right_shoulder.index,
            -shoulder_diff(signal),
            -neck_diff(signal),
            signal.score,
            signal.break_time or signal.right_shoulder.time,
        ),
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
