from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd


@dataclass
class HeadShoulderTopConfig:
    pivot_left: int = 3
    pivot_right: int = 3
    min_head_above_shoulder_pct: float = 0.03
    max_shoulder_diff_pct: float = 0.05
    max_neck_diff_pct: float = 0.05
    min_right_leg_to_left_leg_ratio: float = 0.8
    max_right_leg_to_left_leg_ratio: float = 1.6
    min_head_to_right_neck_to_left_neck_to_head_ratio: float = 0.8
    max_head_to_right_neck_to_left_neck_to_head_ratio: float = 1.6
    min_right_shoulder_ratio_to_left: float = 0.85
    right_shoulder_must_below_head: bool = True
    enable_right_shoulder_volume_weak: bool = True
    right_shoulder_volume_ratio: float = 0.8
    volume_compare_window: int = 10
    enable_break_volume_confirm: bool = True
    break_volume_window: int = 20
    break_volume_ratio: float = 1.2
    enable_ma_filter: bool = True
    ma_short: int = 3
    ma_mid: int = 5
    ma_long: int = 8
    ma_periods: list[int] = field(default_factory=lambda: [5, 10, 20, 30, 60, 250])
    require_ma_bearish_alignment: bool = True
    require_close_below_ma_long: bool = True
    enable_macd_divergence: bool = True
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
    break_time: pd.Timestamp | None = None
    break_price: float | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "pattern": self.pattern,
            "left_shoulder": self.left_shoulder.to_dict(),
            "left_neck": self.left_neck.to_dict(),
            "head": self.head.to_dict(),
            "right_neck": self.right_neck.to_dict(),
            "right_shoulder": self.right_shoulder.to_dict(),
            "neckline_price": self.neckline_price,
            "confirmed": self.confirmed,
            "score": self.score,
            "reasons": self.reasons,
            "break_time": self.break_time.isoformat() if self.break_time is not None else None,
            "break_price": self.break_price,
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


def iter_pattern_candidates(
    pivots: list[PivotPoint],
    kinds: list[str],
) -> list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]]:
    candidates: list[tuple[PivotPoint, PivotPoint, PivotPoint, PivotPoint, PivotPoint]] = []
    seen: set[tuple[int, int, int, int, int]] = set()

    for left_shoulder_index in range(len(pivots) - 4):
        left_shoulder = pivots[left_shoulder_index]
        left_neck = pivots[left_shoulder_index + 1]
        if left_shoulder.kind != kinds[0] or left_neck.kind != kinds[1]:
            continue

        for head_index in range(left_shoulder_index + 2, len(pivots) - 2):
            head = pivots[head_index]
            right_neck = pivots[head_index + 1]
            right_shoulder = pivots[head_index + 2]
            if head.kind != kinds[2] or right_neck.kind != kinds[3] or right_shoulder.kind != kinds[4]:
                continue
            key = (left_shoulder.index, left_neck.index, head.index, right_neck.index, right_shoulder.index)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((left_shoulder, left_neck, head, right_neck, right_shoulder))

    return candidates


def validate_head_shoulders_structure(points: list[PivotPoint], config: HeadShoulderTopConfig) -> tuple[bool, list[str], int]:
    if len(points) != 5:
        return False, ["关键点数量不是5个"], 0
    p1, p2, p3, p4, p5 = points
    if [p.kind for p in points] != ["high", "low", "high", "low", "high"]:
        return False, ["结构不是高-低-高-低-高"], 0

    min_required_head = max(p1.price, p5.price) * (1 + config.min_head_above_shoulder_pct)
    if p3.price < min_required_head:
        return False, [f"头部不够明显，要求至少高于肩部 {config.min_head_above_shoulder_pct * 100:.2f}%"], 0

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
    neck_head_ratio = head_to_right_neck_bars / left_neck_to_head_bars
    if (
        neck_head_ratio < config.min_head_to_right_neck_to_left_neck_to_head_ratio
        or neck_head_ratio > config.max_head_to_right_neck_to_left_neck_to_head_ratio
    ):
        return False, [
            f"头部到右颈K线数量不匹配，当前为左颈到头部的 {neck_head_ratio:.2f} 倍，"
            f"要求 {config.min_head_to_right_neck_to_left_neck_to_head_ratio:.2f}-"
            f"{config.max_head_to_right_neck_to_left_neck_to_head_ratio:.2f} 倍"
        ], 0

    return True, [
        "头部明显高于左右肩",
        f"左右肩高度接近，差异 {shoulder_diff * 100:.2f}%",
        f"两个颈线低点接近，差异 {neck_diff * 100:.2f}%",
        f"右颈到右肩K线数量为左肩到左颈的 {leg_ratio:.2f} 倍",
        f"头部到右颈K线数量为左颈到头部的 {neck_head_ratio:.2f} 倍",
        "右肩没有过度走弱",
        "右肩低于头部",
    ], 60


def iter_head_range_candidates(
    pivots: list[PivotPoint],
    config: HeadShoulderTopConfig,
) -> list[tuple[PivotPoint, PivotPoint, list[PivotPoint], PivotPoint, PivotPoint]]:
    candidates: list[tuple[PivotPoint, PivotPoint, list[PivotPoint], PivotPoint, PivotPoint]] = []
    seen: set[tuple[int, int, int, int, int]] = set()

    for left_shoulder_index in range(len(pivots) - 6):
        left_shoulder = pivots[left_shoulder_index]
        left_neck = pivots[left_shoulder_index + 1]
        if left_shoulder.kind != "high" or left_neck.kind != "low":
            continue

        for right_neck_index in range(left_shoulder_index + 5, len(pivots) - 1):
            right_neck = pivots[right_neck_index]
            if right_neck.kind != "low" or pivots[right_neck_index + 1].kind != "high":
                continue

            right_shoulder_group: list[PivotPoint] = []
            for pivot in pivots[right_neck_index + 1 :]:
                if pivot.kind != "high":
                    break
                right_shoulder_group.append(pivot)
            if not right_shoulder_group:
                continue

            right_shoulder = right_shoulder_group[-1]
            min_required_head = max(left_shoulder.price, right_shoulder.price) * (1 + config.min_head_above_shoulder_pct)
            head_pivots = [
                pivot for pivot in pivots[left_shoulder_index + 2 : right_neck_index]
                if pivot.kind == "high"
                and pivot.price >= min_required_head
            ]
            if len(head_pivots) < 2:
                continue

            key = (
                left_shoulder.index,
                left_neck.index,
                head_pivots[0].index,
                right_neck.index,
                right_shoulder.index,
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append((left_shoulder, left_neck, head_pivots, right_neck, right_shoulder))

    return candidates


def validate_head_range_structure(
    left_shoulder: PivotPoint,
    left_neck: PivotPoint,
    head_pivots: list[PivotPoint],
    right_neck: PivotPoint,
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, list[str], int]:
    if len(head_pivots) < 2:
        return False, ["头部区间高点数量不足"], 0

    head_low = min(point.price for point in head_pivots)
    head_high = max(point.price for point in head_pivots)
    min_required_head = max(left_shoulder.price, right_shoulder.price) * (1 + config.min_head_above_shoulder_pct)
    if head_low < min_required_head:
        return False, [f"头部区间最低高点不够明显，要求至少高于肩部 {config.min_head_above_shoulder_pct * 100:.2f}%"], 0

    shoulder_diff = abs(left_shoulder.price - right_shoulder.price) / max(left_shoulder.price, right_shoulder.price)
    if shoulder_diff > config.max_shoulder_diff_pct:
        return False, [f"左右肩差异过大，当前 {shoulder_diff * 100:.2f}%"], 0

    neck_diff = abs(left_neck.price - right_neck.price) / max(left_neck.price, right_neck.price)
    if neck_diff > config.max_neck_diff_pct:
        return False, [f"两个颈线低点差异过大，当前 {neck_diff * 100:.2f}%"], 0

    if right_shoulder.price < left_shoulder.price * config.min_right_shoulder_ratio_to_left:
        return False, ["右肩过低，更像直接下跌，不像头部区间型头肩顶"], 0

    if right_shoulder.price >= head_low:
        return False, ["右肩进入头部区间，头部区间型头肩顶结构不成立"], 0

    first_head = min(head_pivots, key=lambda point: point.index)
    last_head = max(head_pivots, key=lambda point: point.index)
    left_leg_bars = max(1, left_neck.index - left_shoulder.index)
    right_leg_bars = max(1, right_shoulder.index - right_neck.index)
    leg_ratio = right_leg_bars / left_leg_bars
    if leg_ratio < config.min_right_leg_to_left_leg_ratio or leg_ratio > config.max_right_leg_to_left_leg_ratio:
        return False, [
            f"右颈到右肩K线数量不匹配，当前为左肩到左颈的 {leg_ratio:.2f} 倍，"
            f"要求 {config.min_right_leg_to_left_leg_ratio:.2f}-{config.max_right_leg_to_left_leg_ratio:.2f} 倍"
        ], 0

    left_neck_to_head_bars = max(1, first_head.index - left_neck.index)
    head_to_right_neck_bars = max(1, right_neck.index - last_head.index)
    neck_head_ratio = head_to_right_neck_bars / left_neck_to_head_bars
    if (
        neck_head_ratio < config.min_head_to_right_neck_to_left_neck_to_head_ratio
        or neck_head_ratio > config.max_head_to_right_neck_to_left_neck_to_head_ratio
    ):
        return False, [
            f"头部区间到右颈K线数量不匹配，当前为左颈到头部区间的 {neck_head_ratio:.2f} 倍，"
            f"要求 {config.min_head_to_right_neck_to_left_neck_to_head_ratio:.2f}-"
            f"{config.max_head_to_right_neck_to_left_neck_to_head_ratio:.2f} 倍"
        ], 0

    range_quality = (head_high - head_low) / max(head_high, 1)
    return True, [
        f"头部区间最低高点高于左右肩，区间高点数 {len(head_pivots)}",
        f"头部区间高低差 {range_quality * 100:.2f}%",
        f"左右肩高度接近，差异 {shoulder_diff * 100:.2f}%",
        f"两个颈线低点接近，差异 {neck_diff * 100:.2f}%",
        f"右颈到右肩K线数量为左肩到左颈的 {leg_ratio:.2f} 倍",
        f"头部区间到右颈K线数量为左颈到头部区间的 {neck_head_ratio:.2f} 倍",
        "右肩没有过度走弱",
        "右肩未进入头部区间",
    ], 70


def validate_inverse_head_shoulders_structure(
    points: list[PivotPoint],
    config: HeadShoulderTopConfig,
) -> tuple[bool, list[str], int]:
    if len(points) != 5:
        return False, ["Key point count is not 5"], 0
    p1, p2, p3, p4, p5 = points
    if [p.kind for p in points] != ["low", "high", "low", "high", "low"]:
        return False, ["Structure is not low-high-low-high-low"], 0

    max_required_head = min(p1.price, p5.price) * (1 - config.min_head_above_shoulder_pct)
    if p3.price > max_required_head:
        return False, [f"Head is not low enough; require at least {config.min_head_above_shoulder_pct * 100:.2f}% below shoulders"], 0

    shoulder_diff = abs(p1.price - p5.price) / max(p1.price, p5.price)
    if shoulder_diff > config.max_shoulder_diff_pct:
        return False, [f"Left and right shoulder diff too large: {shoulder_diff * 100:.2f}%"], 0

    neck_diff = abs(p2.price - p4.price) / max(p2.price, p4.price)
    if neck_diff > config.max_neck_diff_pct:
        return False, [f"Two neckline highs differ too much: {neck_diff * 100:.2f}%"], 0

    max_right_shoulder_price = p1.price * (2 - config.min_right_shoulder_ratio_to_left)
    if p5.price > max_right_shoulder_price:
        return False, ["Right shoulder is too high for a standard inverse head-and-shoulders"], 0

    if config.right_shoulder_must_below_head and p5.price <= p3.price:
        return False, ["Right shoulder is lower than or equal to the head; inverse structure is invalid"], 0

    left_leg_bars = max(1, p2.index - p1.index)
    right_leg_bars = max(1, p5.index - p4.index)
    leg_ratio = right_leg_bars / left_leg_bars
    if leg_ratio < config.min_right_leg_to_left_leg_ratio or leg_ratio > config.max_right_leg_to_left_leg_ratio:
        return False, [
            f"Right-neck to right-shoulder bar count mismatch: {leg_ratio:.2f}x of left-shoulder to left-neck, "
            f"required {config.min_right_leg_to_left_leg_ratio:.2f}-{config.max_right_leg_to_left_leg_ratio:.2f}x"
        ], 0

    left_neck_to_head_bars = max(1, p3.index - p2.index)
    head_to_right_neck_bars = max(1, p4.index - p3.index)
    neck_head_ratio = head_to_right_neck_bars / left_neck_to_head_bars
    if (
        neck_head_ratio < config.min_head_to_right_neck_to_left_neck_to_head_ratio
        or neck_head_ratio > config.max_head_to_right_neck_to_left_neck_to_head_ratio
    ):
        return False, [
            f"Head to right-neck bar count mismatch: {neck_head_ratio:.2f}x of left-neck to head, "
            f"required {config.min_head_to_right_neck_to_left_neck_to_head_ratio:.2f}-"
            f"{config.max_head_to_right_neck_to_left_neck_to_head_ratio:.2f}x"
        ], 0

    return True, [
        "Head is clearly below both shoulders",
        f"Left and right shoulders are close; diff {shoulder_diff * 100:.2f}%",
        f"Two neckline highs are close; diff {neck_diff * 100:.2f}%",
        f"Right-neck to right-shoulder bar count is {leg_ratio:.2f}x of left-shoulder to left-neck",
        f"Head to right-neck bar count is {neck_head_ratio:.2f}x of left-neck to head",
        "Right shoulder has not rebounded too far",
        "Right shoulder is above the head",
    ], 60


def check_right_shoulder_volume_weak(
    df: pd.DataFrame,
    head: PivotPoint,
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, str, int]:
    if not config.enable_right_shoulder_volume_weak:
        return True, "未启用右肩缩量过滤", 0
    window = config.volume_compare_window
    head_volume = df.loc[max(0, head.index - window) : min(len(df) - 1, head.index + window), "volume"].mean()
    right_volume = df.loc[max(0, right_shoulder.index - window) : min(len(df) - 1, right_shoulder.index + window), "volume"].mean()
    if head_volume <= 0:
        return False, "头部附近成交量异常", 0
    ratio = right_volume / head_volume
    if ratio <= config.right_shoulder_volume_ratio:
        return True, f"右肩成交量减弱，右肩/头部量能比 {ratio:.2f}", 15
    return False, f"右肩成交量未明显减弱，右肩/头部量能比 {ratio:.2f}", 0


def check_right_shoulder_volume_weak_against_head_range(
    df: pd.DataFrame,
    head_pivots: list[PivotPoint],
    right_shoulder: PivotPoint,
    config: HeadShoulderTopConfig,
) -> tuple[bool, str, int]:
    if not config.enable_right_shoulder_volume_weak:
        return True, "未启用右肩缩量过滤", 0
    if not head_pivots:
        return False, "头部区间为空", 0
    window = config.volume_compare_window
    start = max(0, min(point.index for point in head_pivots) - window)
    end = min(len(df) - 1, max(point.index for point in head_pivots) + window)
    head_volume = df.loc[start:end, "volume"].mean()
    right_volume = df.loc[
        max(0, right_shoulder.index - window) : min(len(df) - 1, right_shoulder.index + window),
        "volume",
    ].mean()
    if head_volume <= 0:
        return False, "头部区间成交量异常", 0
    ratio = right_volume / head_volume
    if ratio <= config.right_shoulder_volume_ratio:
        return True, f"右肩成交量减弱，右肩/头部区间平均量能比 {ratio:.2f}", 15
    return False, f"右肩成交量未明显减弱，右肩/头部区间平均量能比 {ratio:.2f}", 0


def check_ma_bearish_filter(df: pd.DataFrame, index: int, config: HeadShoulderTopConfig) -> tuple[bool, str, int]:
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


def check_macd_head_range_top_divergence(
    df: pd.DataFrame,
    left_shoulder: PivotPoint,
    head_pivots: list[PivotPoint],
    config: HeadShoulderTopConfig,
) -> tuple[bool, str, int]:
    if not config.enable_macd_divergence:
        return True, "未启用 MACD 顶背离过滤", 0
    if not head_pivots:
        return False, "头部区间为空，无法判断 MACD 顶背离", 0
    head_high = max(head_pivots, key=lambda point: point.price)
    if head_high.price <= left_shoulder.price * (1 + config.macd_price_new_high_pct):
        return False, "头部区间最高价没有形成明显新高，不满足 MACD 顶背离前提", 0
    macd_col = "macd_hist" if config.use_macd_hist_for_divergence else "macd_dif"
    macd_name = "MACD柱" if config.use_macd_hist_for_divergence else "DIF"
    left_macd = df.loc[left_shoulder.index, macd_col]
    head_macd = df.loc[head_high.index, macd_col]
    if pd.isna(left_macd) or pd.isna(head_macd):
        return False, "MACD 数据不足", 0
    if head_macd < left_macd:
        return True, f"出现 MACD 顶背离：头部区间最高价创新高，但最高价对应{macd_name}降低", 15
    return False, f"未出现 MACD 顶背离：头部区间最高价对应{macd_name}未降低", 0


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
        if config.enable_break_volume_confirm:
            if i < config.break_volume_window:
                continue
            avg_volume = df.loc[i - config.break_volume_window : i - 1, "volume"].mean()
            if avg_volume <= 0:
                continue
            volume_ratio = df.loc[i, "volume"] / avg_volume
            if volume_ratio < config.break_volume_ratio:
                continue
            return True, i, df.loc[i, "datetime"], break_price, (
                f"跌破颈线确认，跌破价 {break_price:.2f}，颈线价 {neckline_price:.2f}，"
                f"成交量放大 {volume_ratio:.2f} 倍"
            ), 20
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
        if config.enable_break_volume_confirm:
            if i < config.break_volume_window:
                continue
            avg_volume = df.loc[i - config.break_volume_window : i - 1, "volume"].mean()
            if avg_volume <= 0:
                continue
            volume_ratio = df.loc[i, "volume"] / avg_volume
            if volume_ratio < config.break_volume_ratio:
                continue
            return True, i, df.loc[i, "datetime"], break_price, (
                f"突破颈线确认，突破价 {break_price:.2f}，颈线价 {neckline_price:.2f}，"
                f"成交量放大 {volume_ratio:.2f} 倍"
            ), 20
        return True, i, df.loc[i, "datetime"], break_price, (
            f"突破颈线确认，突破价 {break_price:.2f}，颈线价 {neckline_price:.2f}"
        ), 15
    return False, None, None, None, "尚未有效突破颈线", 0


def cap_score(score: int) -> int:
    return min(score, 100)


def scan_head_shoulders_top(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
) -> list[HeadShoulderTopSignal]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = compress_pivots(find_pivots(df, left=config.pivot_left, right=config.pivot_right))
    signals: list[HeadShoulderTopSignal] = []

    for p1, p2, p3, p4, p5 in iter_pattern_candidates(pivots, ["high", "low", "high", "low", "high"]):
        ok, reasons, total_score = validate_head_shoulders_structure([p1, p2, p3, p4, p5], config)
        if not ok:
            continue

        ok, reason, score = check_right_shoulder_volume_weak(df, p3, p5, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        ok, reason, score = check_macd_top_divergence(df, p1, p3, config)
        reasons.append(reason)
        if ok:
            total_score += score

        confirmed, break_index, break_time, break_price, reason, score = check_neckline_break(df, p2, p4, p5, config)
        if not confirmed:
            neckline_price = calculate_neckline_price(p2, p4, p5.index)
            signals.append(HeadShoulderTopSignal(
                symbol=symbol,
                timeframe=timeframe,
                pattern="head_shoulders_top",
                left_shoulder=p1,
                left_neck=p2,
                head=p3,
                right_neck=p4,
                right_shoulder=p5,
                neckline_price=neckline_price,
                confirmed=False,
                score=cap_score(total_score),
                reasons=reasons + [reason],
                message=f"{symbol} {timeframe} 疑似头肩顶，等待跌破颈线确认，当前评分 {cap_score(total_score)}",
            ))
            continue

        total_score += score
        reasons.append(reason)
        assert break_index is not None
        ok, reason, score = check_ma_bearish_filter(df, break_index, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        if config.enable_score and cap_score(total_score) < config.min_score_to_alert:
            continue

        neckline_price = calculate_neckline_price(p2, p4, break_index)
        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="head_shoulders_top",
            left_shoulder=p1,
            left_neck=p2,
            head=p3,
            right_neck=p4,
            right_shoulder=p5,
            neckline_price=neckline_price,
            confirmed=True,
            score=cap_score(total_score),
            reasons=reasons,
            break_time=break_time,
            break_price=break_price,
            message=(
                f"{symbol} {timeframe} 头肩顶确认，评分 {cap_score(total_score)}。"
                f"跌破价格 {break_price:.2f}，颈线价 {neckline_price:.2f}。"
            ),
        ))

    return signals


def scan_head_shoulders_range_top(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
) -> list[HeadShoulderTopSignal]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = find_pivots(df, left=config.pivot_left, right=config.pivot_right)
    signals: list[HeadShoulderTopSignal] = []

    for left_shoulder, left_neck, head_pivots, right_neck, right_shoulder in iter_head_range_candidates(pivots, config):
        ok, reasons, total_score = validate_head_range_structure(
            left_shoulder,
            left_neck,
            head_pivots,
            right_neck,
            right_shoulder,
            config,
        )
        if not ok:
            continue

        head = max(head_pivots, key=lambda point: point.price)
        ok, reason, score = check_right_shoulder_volume_weak_against_head_range(df, head_pivots, right_shoulder, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        ok, reason, score = check_macd_head_range_top_divergence(df, left_shoulder, head_pivots, config)
        reasons.append(reason)
        if ok:
            total_score += score

        confirmed, break_index, break_time, break_price, reason, score = check_neckline_break(
            df,
            left_neck,
            right_neck,
            right_shoulder,
            config,
        )
        if not confirmed:
            neckline_price = calculate_neckline_price(left_neck, right_neck, right_shoulder.index)
            signals.append(HeadShoulderTopSignal(
                symbol=symbol,
                timeframe=timeframe,
                pattern="head_shoulders_range_top",
                left_shoulder=left_shoulder,
                left_neck=left_neck,
                head=head,
                right_neck=right_neck,
                right_shoulder=right_shoulder,
                neckline_price=neckline_price,
                confirmed=False,
                score=cap_score(total_score),
                reasons=reasons + [reason],
                message=f"{symbol} {timeframe} 疑似头部区间型头肩顶，等待跌破颈线确认，当前评分 {cap_score(total_score)}",
            ))
            continue

        total_score += score
        reasons.append(reason)
        assert break_index is not None
        ok, reason, score = check_ma_bearish_filter(df, break_index, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        if config.enable_score and cap_score(total_score) < config.min_score_to_alert:
            continue

        neckline_price = calculate_neckline_price(left_neck, right_neck, break_index)
        signals.append(HeadShoulderTopSignal(
            symbol=symbol,
            timeframe=timeframe,
            pattern="head_shoulders_range_top",
            left_shoulder=left_shoulder,
            left_neck=left_neck,
            head=head,
            right_neck=right_neck,
            right_shoulder=right_shoulder,
            neckline_price=neckline_price,
            confirmed=True,
            score=cap_score(total_score),
            reasons=reasons,
            break_time=break_time,
            break_price=break_price,
            message=(
                f"{symbol} {timeframe} 头部区间型头肩顶确认，评分 {cap_score(total_score)}。"
                f"跌破价格 {break_price:.2f}，颈线价 {neckline_price:.2f}。"
            ),
        ))

    return signals


def scan_inverse_head_shoulders(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
) -> list[HeadShoulderTopSignal]:
    df = df.copy().reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = add_macd_columns(add_ma_columns(df, config), config)
    pivots = compress_pivots(find_pivots(df, left=config.pivot_left, right=config.pivot_right))
    signals: list[HeadShoulderTopSignal] = []

    for p1, p2, p3, p4, p5 in iter_pattern_candidates(pivots, ["low", "high", "low", "high", "low"]):
        ok, reasons, total_score = validate_inverse_head_shoulders_structure([p1, p2, p3, p4, p5], config)
        if not ok:
            continue

        ok, reason, score = check_right_shoulder_volume_weak(df, p3, p5, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        ok, reason, score = check_macd_bottom_divergence(df, p1, p3, config)
        reasons.append(reason)
        if ok:
            total_score += score

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
                score=cap_score(total_score),
                reasons=reasons + [reason],
                message=f"{symbol} {timeframe} 疑似反向头肩顶，等待突破颈线确认，当前评分 {cap_score(total_score)}",
            ))
            continue

        total_score += score
        reasons.append(reason)
        assert break_index is not None
        ok, reason, score = check_ma_bullish_filter(df, break_index, config)
        if not ok:
            continue
        reasons.append(reason)
        total_score += score

        if config.enable_score and cap_score(total_score) < config.min_score_to_alert:
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
            score=cap_score(total_score),
            reasons=reasons,
            break_time=break_time,
            break_price=break_price,
            message=(
                f"{symbol} {timeframe} 反向头肩顶确认，评分 {cap_score(total_score)}。"
                f"突破价格 {break_price:.2f}，颈线价 {neckline_price:.2f}。"
            ),
        ))

    return signals


def scan_head_shoulders(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: HeadShoulderTopConfig,
) -> list[HeadShoulderTopSignal]:
    signals = scan_head_shoulders_top(df, symbol=symbol, timeframe=timeframe, config=config)
    signals.extend(scan_head_shoulders_range_top(df, symbol=symbol, timeframe=timeframe, config=config))
    signals.extend(scan_inverse_head_shoulders(df, symbol=symbol, timeframe=timeframe, config=config))
    if config.max_signal_age_bars > 0:
        min_right_shoulder_index = max(0, len(df) - config.max_signal_age_bars)
        signals = [
            signal for signal in signals
            if signal.right_shoulder.index >= min_right_shoulder_index
        ]
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
