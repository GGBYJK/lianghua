from __future__ import annotations

from app.monitor import build_wechat_workbot_content


def test_wechat_workbot_content_uses_inverse_head_shoulders_example_format() -> None:
    signal = {
        "symbol": "a2607",
        "timeframe": "1m",
        "pattern": "inverse_head_shoulders",
        "alert_type": "right_shoulder_confirmed",
        "score": 88,
        "right_shoulder": {"time": "2026-05-25T14:54:00", "price": 3329},
    }

    assert (
        build_wechat_workbot_content(signal, {"name": "a2607"})
        == "\u65b0\u5f62\u6001\uff1aa2607\uff0c1m\uff0c\u53cd\u5411\u5934\u80a9\uff0c20260525 14:54"
    )
