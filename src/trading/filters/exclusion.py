"""매수 제외 하드필터.

스코어링 이전에 적용되는 규칙 기반 필터로, 하나라도 해당되면 스코어와 무관하게 즉시 제외한다.
'점수는 높지만 구조적으로 매매하면 안 되는 종목'을 걸러내는 안전판 역할이다.
"""
from __future__ import annotations

import numpy as np

from trading.config import Config, get_config
from trading.data.interfaces import MarketSnapshot
from trading.indicators import technical as ta

SESSION_MINUTES = 390  # 09:00~15:30


def is_excluded(snapshot: MarketSnapshot, phase: str, config: Config | None = None) -> tuple[bool, list[str]]:
    cfg = (config or get_config()).filters
    reasons: list[str] = []

    if set(snapshot.admin_flags) & set(cfg.get("exclude_flags", [])):
        reasons.append(f"관리/경고 플래그: {snapshot.admin_flags}")

    if snapshot.avg_trading_value_20d_krw < cfg.get("min_avg_trading_value_krw", 0):
        reasons.append("유동성 부족 (평균 거래대금 미달)")

    elapsed_min = len(snapshot.minute_bars)
    expected_min_value = cfg.get("min_today_trading_value_krw", 0) * elapsed_min / SESSION_MINUTES
    if elapsed_min >= 5 and snapshot.today_trading_value_krw < expected_min_value:
        reasons.append("당일 거래대금 부족")

    if snapshot.spread_pct > cfg.get("max_spread_pct", 999):
        reasons.append(f"스프레드 과다 ({snapshot.spread_pct:.2f}%)")

    # 호가 잔량이 둘 다 정확히 0이면 "실제로 얇다"가 아니라 "호가 데이터 자체가 없다"는
    # 뜻이다(예: 해외주식은 호가창 조회를 아직 구현하지 않아 항상 0으로 채워진다).
    # 이 경우 필터를 적용하면 모든 종목이 무조건 걸려버리므로, 데이터가 있을 때만 검사한다.
    if snapshot.bid_qty_top5 > 0 or snapshot.ask_qty_top5 > 0:
        depth_krw = (snapshot.bid_qty_top5 + snapshot.ask_qty_top5) * snapshot.last_close
        if depth_krw < cfg.get("min_orderbook_depth_krw", 0):
            reasons.append("호가 잔량 과소 (호가 얇음)")

    overheat_pct = (snapshot.last_close / snapshot.today_open - 1) * 100 if snapshot.today_open > 0 else 0
    if overheat_pct > cfg.get("max_overheat_return_pct", 999):
        reasons.append(f"시가 대비 과열 ({overheat_pct:.1f}%)")

    mbars = snapshot.minute_bars
    if len(mbars) >= 14:
        atr_val = ta.atr(mbars["high"], mbars["low"], mbars["close"], window=14).iloc[-1]
        atr_pct = atr_val / snapshot.last_close * 100 if snapshot.last_close > 0 else 0
        if atr_pct > cfg.get("max_volatility_atr_pct", 999):
            reasons.append(f"변동성 과다 (ATR {atr_pct:.1f}%)")

    if len(mbars) >= 4:
        recent_ret = (mbars["close"].iloc[-1] / mbars["close"].iloc[-4] - 1) * 100
        vol_ratio = ta.volume_ratio(mbars["volume"], window=20).iloc[-1]
        if recent_ret > 5.0 and vol_ratio < cfg.get("news_spike_volume_ratio_threshold", 1.5):
            reasons.append("거래량 뒷받침 없는 단독 급등 의심 (뉴스성)")

    if phase == "trend_pullback":
        daily = snapshot.daily_bars
        if len(daily) >= 15:
            adx_val = ta.adx(daily["high"], daily["low"], daily["close"], window=14).iloc[-1]
            if adx_val < cfg.get("min_adx_for_trend", 0):
                reasons.append(f"추세 부재 (횡보, ADX {adx_val:.1f})")

    return len(reasons) > 0, reasons
