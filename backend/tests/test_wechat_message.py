from __future__ import annotations

from app.monitor import build_wechat_workbot_content


def test_wechat_workbot_content_uses_inverse_head_shoulders_example_format() -> None:
    signal = {
        "symbol": "a2607",
        "timeframe": "1m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "score": 88,
        "pattern_score": 77,
        "pattern_metrics": {"stop": 3301.125, "target": 3368.875},
        "right_shoulder": {"time": "2026-05-25T14:54:00", "price": 3329},
    }

    assert (
        build_wechat_workbot_content(signal, {"name": "a2607"})
        == "反向头肩：a2607  1m\n"
        "时间：20260525   14:54\n"
        "评分：77+88   强多头趋势\n"
        "止损价：3301.12\n"
        "目标价：3368.88"
    )


def test_wechat_workbot_content_matches_requested_top_format() -> None:
    signal = {
        "symbol": "DCE.b2609",
        "timeframe": "5m",
        "pattern": "head_shoulders_top",
        "alert_type": "right_shoulder_confirmed",
        "score": 80,
        "pattern_score": 77,
        "trend_label": "空头趋势",
        "retest_time": "2026-06-22T22:55:00",
        "pattern_metrics": {"stop": 3120.66, "target": 3103.46},
    }

    assert (
        build_wechat_workbot_content(signal, {"name": "DCE.b2609"})
        == "头肩顶：DCE.b2609  5m\n"
        "时间：20260622   22:55\n"
        "评分：77+80   空头趋势\n"
        "止损价：3120.66\n"
        "目标价：3103.46"
    )
