"""MarketSnapshot으로부터 스코어링용 피처를 추출한다.

모든 피처는 [-1, 1] 범위로 정규화한다 (+1: 진입에 강하게 유리, -1: 강하게 불리).
이렇게 통일하면 가중치(weights.json)가 순수하게 '중요도'만 의미하게 되어,
ML 최적화(로지스틱회귀 계수)와 수작업 튜닝을 같은 스케일에서 다룰 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

from trading.config import Config, get_config
from trading.data.interfaces import MarketSnapshot
from trading.indicators import technical as ta

FEATURE_NAMES = [
    "volume_surge",
    "trading_value_rank",
    "execution_strength",
    "orderbook_imbalance",
    "program_net_buy",
    "foreign_inst_net_buy",
    "candle_strength",
    "minute_trend",
    "daily_position",
    "prior_high_breakout",
    "vi_triggered",
    "sector_strength",
    "theme_strength",
    "index_direction",
    "volatility",
    "avg_trading_value",
    "spread",
    "liquidity",
]


@dataclass
class FeatureVector:
    volume_surge: float
    trading_value_rank: float
    execution_strength: float
    orderbook_imbalance: float
    program_net_buy: float
    foreign_inst_net_buy: float
    candle_strength: float
    minute_trend: float
    daily_position: float
    prior_high_breakout: float
    vi_triggered: float
    sector_strength: float
    theme_strength: float
    index_direction: float
    volatility: float
    avg_trading_value: float
    spread: float
    liquidity: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def as_array(self) -> np.ndarray:
        return np.array([getattr(self, name) for name in FEATURE_NAMES], dtype=float)


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(np.clip(x, lo, hi))


class FeatureExtractor:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()

    def extract_single(self, snapshot: MarketSnapshot, trading_value_rank_pct: float = 0.5) -> FeatureVector:
        """단일 스냅샷 기준 피처 추출. trading_value_rank_pct는 유니버스 내 백분위(0~1),
        배치 컨텍스트가 없으면 0.5(중간값)로 둔다 -> extract_batch 사용을 권장."""
        mbars = snapshot.minute_bars
        close = mbars["close"]
        volume = mbars["volume"]

        # 1) 거래량 급증
        vol_ratio = ta.volume_ratio(volume, window=20).iloc[-1]
        volume_surge = _clip(np.tanh((vol_ratio - 1) / 2))

        # 2) 거래대금 상위 (유니버스 백분위, extract_batch에서 정확히 계산됨)
        trading_value_rank = _clip(2 * trading_value_rank_pct - 1)

        # 3) 체결강도
        execution_strength = _clip(np.tanh((snapshot.execution_strength - 100) / 40))

        # 4) 호가 잔량 불균형
        bq, aq = snapshot.bid_qty_top5, snapshot.ask_qty_top5
        orderbook_imbalance = _clip((bq - aq) / max(bq + aq, 1))

        # 5) 프로그램 순매수
        program_net_buy = _clip(np.tanh(snapshot.program_net_buy_krw / 3_000_000_000))

        # 6) 외국인/기관 순매수
        foreign_inst_net_buy = _clip(
            np.tanh((snapshot.foreign_net_buy_krw + snapshot.institution_net_buy_krw) / 3_000_000_000)
        )

        # 7) 캔들의 힘 (최근 3분봉 평균)
        cs = ta.candle_strength(mbars["open"], mbars["high"], mbars["low"], close)
        candle_strength = _clip(cs.tail(3).mean())

        # 8) 분봉 추세
        slope = ta.minute_trend_slope(close, window=5).iloc[-1]
        minute_trend = _clip(slope * 50)

        # 9) 일봉 위치 (5일선/기준선 대비)
        daily = snapshot.daily_bars
        last_close = snapshot.last_close
        if len(daily) >= 2:
            sma5 = ta.sma(daily["close"], 5).iloc[-1]
            baseline = ta.ichimoku_baseline(daily["high"], daily["low"], 26).iloc[-1]
            above_sma5 = (last_close / sma5 - 1) if sma5 > 0 else 0.0
            above_baseline = (last_close / baseline - 1) if baseline > 0 else 0.0
            daily_position = _clip(np.tanh((above_sma5 * 8 + above_baseline * 8) / 2))
        else:
            daily_position = 0.0

        # 10) 전고점 돌파 여부 (당일 장중 고점 + 최근 5일 고점 중 더 큰 값 기준)
        intraday_prior_high = ta.rolling_high(mbars["high"], window=len(mbars)).iloc[-1]
        recent_daily_high = daily["high"].tail(5).max() if len(daily) > 0 else np.nan
        candidates = [v for v in [intraday_prior_high, recent_daily_high] if v == v]  # drop NaN
        prior_high = max(candidates) if candidates else last_close
        if last_close > prior_high:
            prior_high_breakout = 1.0
        else:
            prior_high_breakout = _clip((last_close / prior_high - 1) * 20, -1.0, 0.3)

        # 11) VI 발생 여부
        vi_triggered = 1.0 if snapshot.vi_triggered else 0.0

        # 12/13) 섹터/테마 강도 (지수 대비 상대수익률)
        sector_strength = _clip(np.tanh((snapshot.sector_return_pct - snapshot.index_return_pct) / 3))
        theme_strength = _clip(np.tanh((snapshot.theme_return_pct - snapshot.index_return_pct) / 3))

        # 14) 시장 지수 방향
        index_direction = _clip(np.tanh(snapshot.index_return_pct / 0.5))

        # 15) 변동성 (ATR/가격, 과도한 변동성은 페널티)
        atr_val = ta.atr(mbars["high"], mbars["low"], close, window=14).iloc[-1]
        atr_pct = (atr_val / last_close * 100) if last_close > 0 else 0.0
        max_vol = self.config.filters.get("max_volatility_atr_pct", 8.0)
        volatility = _clip(-np.tanh((atr_pct - max_vol / 2) / max_vol))

        # 16) 평균 거래대금 (유동성 baseline)
        min_avg_value = self.config.filters.get("min_avg_trading_value_krw", 3_000_000_000)
        avg_trading_value = _clip(np.tanh((snapshot.avg_trading_value_20d_krw / min_avg_value - 1) / 2))

        # 17) 스프레드 (좁을수록 좋음)
        max_spread = self.config.filters.get("max_spread_pct", 0.5)
        spread = _clip(-np.tanh(snapshot.spread_pct / max_spread))

        # 18) 유동성 (호가 depth 대비 최소 기준)
        min_depth = self.config.filters.get("min_orderbook_depth_krw", 30_000_000)
        depth_krw = (bq + aq) * last_close
        liquidity = _clip(np.tanh(depth_krw / min_depth - 1))

        return FeatureVector(
            volume_surge=volume_surge,
            trading_value_rank=trading_value_rank,
            execution_strength=execution_strength,
            orderbook_imbalance=orderbook_imbalance,
            program_net_buy=program_net_buy,
            foreign_inst_net_buy=foreign_inst_net_buy,
            candle_strength=candle_strength,
            minute_trend=minute_trend,
            daily_position=daily_position,
            prior_high_breakout=prior_high_breakout,
            vi_triggered=vi_triggered,
            sector_strength=sector_strength,
            theme_strength=theme_strength,
            index_direction=index_direction,
            volatility=volatility,
            avg_trading_value=avg_trading_value,
            spread=spread,
            liquidity=liquidity,
        )

    def extract_batch(self, snapshots: dict[str, MarketSnapshot]) -> dict[str, FeatureVector]:
        """유니버스 전체 스냅샷을 받아 거래대금 순위 등 상대지표까지 정확히 계산한다."""
        values = {sym: snap.today_trading_value_krw for sym, snap in snapshots.items()}
        ranked = sorted(values, key=lambda s: values[s])
        n = len(ranked)
        pct_rank = {sym: (i + 1) / n for i, sym in enumerate(ranked)} if n > 0 else {}

        return {
            sym: self.extract_single(snap, trading_value_rank_pct=pct_rank.get(sym, 0.5))
            for sym, snap in snapshots.items()
        }
