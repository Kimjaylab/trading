"""시장 국면(상승장/하락장/횡보장) 분류.

코스피/코스닥 등 지수 종가 시계열만으로 판단한다 (개별 종목 스코어링과 독립).
국면에 따라 스코어링 임계값을 가산/감산해 하락장에서는 더 보수적으로,
상승장에서는 조금 더 공격적으로 진입하도록 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from trading.config import Config, get_config
from trading.indicators import technical as ta


class Regime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


@dataclass
class RegimeResult:
    regime: Regime
    ma_short: float
    ma_long: float
    slope_pct: float
    threshold_adjustment: float


class MarketRegimeClassifier:
    def __init__(self, config: Config | None = None):
        self.config = config or get_config()

    def classify(self, index_close: pd.Series) -> RegimeResult:
        cfg = self.config.regime
        short_w = cfg.get("index_ma_short", 5)
        long_w = cfg.get("index_ma_long", 20)

        ma_short = ta.sma(index_close, short_w)
        ma_long = ta.sma(index_close, long_w)

        if len(ma_long.dropna()) < max(long_w // 2, 2):
            return RegimeResult(Regime.SIDEWAYS, float(ma_short.iloc[-1]), float(ma_long.iloc[-1]), 0.0,
                                 cfg["threshold_adjust"]["sideways"])

        long_window = ma_long.tail(long_w)
        slope_pct = float((long_window.iloc[-1] / long_window.iloc[0] - 1) * 100) if long_window.iloc[0] else 0.0

        cross = ma_short.iloc[-1] - ma_long.iloc[-1]
        trend_strength = abs(slope_pct)

        if trend_strength < 0.15:
            regime = Regime.SIDEWAYS
        elif cross > 0 and slope_pct > 0:
            regime = Regime.BULL
        elif cross < 0 and slope_pct < 0:
            regime = Regime.BEAR
        else:
            regime = Regime.SIDEWAYS

        adjustment = cfg["threshold_adjust"][regime.value]
        return RegimeResult(
            regime=regime,
            ma_short=float(ma_short.iloc[-1]),
            ma_long=float(ma_long.iloc[-1]),
            slope_pct=slope_pct,
            threshold_adjustment=adjustment,
        )
