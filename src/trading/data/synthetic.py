"""프레임워크 검증/데모용 합성 시세 데이터 생성기.

실제 KRX 데이터가 없는 환경에서 스코어링·전략·리스크·백테스트 엔진이
올바르게 동작하는지 검증하기 위해, 의도된 시나리오(돌파/눌림목/추세/횡보/유동성부족 등)를
가진 가상의 종목들로 하루 세션을 생성한다. 실데이터 연동 시에는 CSVDataProvider나
실제 API 어댑터로 교체하면 된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from datetime import datetime, timedelta, time
from dataclasses import dataclass

from trading.data.interfaces import MarketDataProvider, MarketSnapshot

SCENARIOS = ["breakout", "pullback_then_trend", "grinding_uptrend", "choppy", "thin_liquidity", "overheat_fade"]


@dataclass
class SymbolProfile:
    symbol: str
    scenario: str
    base_price: float
    base_volume: int
    sector: str
    theme: str


class SyntheticDataProvider(MarketDataProvider):
    def __init__(
        self,
        session_date: datetime | None = None,
        n_symbols: int = 24,
        seed: int = 42,
        bar_minutes: int = 1,
        market_open: time = time(9, 0),
        market_close: time = time(15, 30),
    ):
        self.rng = np.random.default_rng(seed)
        self.session_date = (session_date or datetime(2026, 7, 16)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.bar_minutes = bar_minutes
        self.market_open_t = market_open
        self.market_close_t = market_close

        self._timestamps = self._build_timestamps()
        self._symbols = self._build_symbol_universe(n_symbols)
        self._index_series = self._build_index_series()
        self._minute_data: dict[str, pd.DataFrame] = {}
        self._daily_data: dict[str, pd.DataFrame] = {}
        self._microstructure: dict[str, pd.DataFrame] = {}
        for prof in self._symbols:
            self._daily_data[prof.symbol] = self._build_daily_history(prof)
            self._minute_data[prof.symbol] = self._build_minute_session(prof)
            self._microstructure[prof.symbol] = self._build_microstructure(prof, self._minute_data[prof.symbol])

    # ---------- construction helpers ----------
    def _build_timestamps(self) -> list[datetime]:
        open_dt = self.session_date.replace(hour=self.market_open_t.hour, minute=self.market_open_t.minute)
        close_dt = self.session_date.replace(hour=self.market_close_t.hour, minute=self.market_close_t.minute)
        if close_dt <= open_dt:
            # 미국장처럼 자정을 넘기는 세션 (예: 22:30 개장 -> 다음날 05:00 마감)
            close_dt += timedelta(days=1)
        n = int((close_dt - open_dt).total_seconds() / 60 / self.bar_minutes)
        return [open_dt + timedelta(minutes=i * self.bar_minutes) for i in range(n)]

    def _build_symbol_universe(self, n: int) -> list[SymbolProfile]:
        sectors = ["반도체", "2차전지", "바이오", "플랫폼", "조선", "자동차"]
        themes = ["AI", "로봇", "전력망", "우주항공", "원자력", "테마없음"]
        profiles = []
        for i in range(n):
            scenario = SCENARIOS[i % len(SCENARIOS)]
            profiles.append(
                SymbolProfile(
                    symbol=f"A{100000 + i}",
                    scenario=scenario,
                    base_price=float(self.rng.uniform(5000, 80000)),
                    base_volume=int(self.rng.uniform(50_000, 2_000_000)),
                    sector=sectors[i % len(sectors)],
                    theme=themes[i % len(themes)],
                )
            )
        return profiles

    def _build_index_series(self) -> pd.Series:
        n = len(self._timestamps)
        drift = self.rng.normal(0.00002, 0.0004, n).cumsum()
        return pd.Series(2600 * (1 + drift), index=self._timestamps)

    def _build_daily_history(self, prof: SymbolProfile, n_days: int = 25) -> pd.DataFrame:
        dates = [self.session_date - timedelta(days=n_days - i) for i in range(n_days)]
        price = prof.base_price
        rows = []
        trend_bias = {
            "breakout": 0.004,
            "pullback_then_trend": 0.006,
            "grinding_uptrend": 0.008,
            "choppy": 0.0,
            "thin_liquidity": -0.001,
            "overheat_fade": 0.01,
        }[prof.scenario]
        for d in dates:
            ret = self.rng.normal(trend_bias, 0.018)
            o = price
            c = price * (1 + ret)
            h = max(o, c) * (1 + abs(self.rng.normal(0, 0.006)))
            low = min(o, c) * (1 - abs(self.rng.normal(0, 0.006)))
            vol = max(1000, int(prof.base_volume * self.rng.uniform(0.6, 1.4)))
            trading_value = vol * (o + c) / 2
            rows.append(dict(date=d, open=o, high=h, low=low, close=c, volume=vol, trading_value=trading_value))
            price = c
        df = pd.DataFrame(rows).set_index("date")
        return df

    def _build_minute_session(self, prof: SymbolProfile) -> pd.DataFrame:
        n = len(self._timestamps)
        last_close = self._daily_data[prof.symbol]["close"].iloc[-1]
        opens, highs, lows, closes, vols = [], [], [], [], []
        price = last_close * (1 + self.rng.normal(0.0, 0.004))
        for i in range(n):
            frac = i / max(n - 1, 1)
            drift, vol_mult = self._scenario_minute_params(prof.scenario, frac)
            ret = self.rng.normal(drift, 0.0025)
            o = price
            c = max(price * (1 + ret), 1.0)
            h = max(o, c) * (1 + abs(self.rng.normal(0, 0.0015)))
            low = min(o, c) * (1 - abs(self.rng.normal(0, 0.0015)))
            base_vol = prof.base_volume / n
            vol = max(1, int(base_vol * vol_mult * self.rng.uniform(0.5, 1.6)))
            opens.append(o)
            highs.append(h)
            lows.append(low)
            closes.append(c)
            vols.append(vol)
            price = c
        return pd.DataFrame(
            dict(open=opens, high=highs, low=lows, close=closes, volume=vols), index=self._timestamps
        )

    @staticmethod
    def _scenario_minute_params(scenario: str, frac: float) -> tuple[float, float]:
        """(수익률 드리프트, 거래량 배율)을 시나리오와 하루 경과비율(frac)에 따라 반환."""
        if scenario == "breakout":
            if frac < 0.03:
                return 0.008, 6.0
            if frac < 0.06:
                return -0.001, 1.2
            return 0.0002, 0.8
        if scenario == "pullback_then_trend":
            if frac < 0.05:
                return 0.006, 4.0
            if frac < 0.15:
                return -0.0015, 0.5
            if frac < 0.25:
                return 0.004, 2.5
            return 0.0008, 1.0
        if scenario == "grinding_uptrend":
            return 0.0009, 1.1
        if scenario == "choppy":
            return 0.0, 1.0
        if scenario == "thin_liquidity":
            return 0.0002, 0.15
        if scenario == "overheat_fade":
            if frac < 0.08:
                return 0.012, 5.0
            return -0.004, 0.6
        return 0.0, 1.0

    def _build_microstructure(self, prof: SymbolProfile, minute_bars: pd.DataFrame) -> pd.DataFrame:
        n = len(minute_bars)
        ret = minute_bars["close"].pct_change().fillna(0.0)
        vol_ratio = (minute_bars["volume"] / max(minute_bars["volume"].mean(), 1)).clip(0, 10)

        exec_strength = (100 + ret * 4000 + self.rng.normal(0, 6, n)).clip(20, 220)
        program = (ret * 3_000_000_000 + self.rng.normal(0, 2_000_000, n)).cumsum()
        foreign = (ret * 2_000_000_000 + self.rng.normal(0, 1_500_000, n)).cumsum()
        inst = (ret * 1_000_000_000 + self.rng.normal(0, 1_000_000, n)).cumsum()

        spread_base = 0.05 if prof.scenario != "thin_liquidity" else 0.9
        spread_pct = np.clip(spread_base + self.rng.normal(0, 0.05, n).clip(min=-0.03), 0.01, None)

        depth_base = prof.base_volume * 0.02 if prof.scenario != "thin_liquidity" else prof.base_volume * 0.0005
        bid_depth = np.maximum(depth_base * self.rng.uniform(0.5, 1.5, n), 1)
        ask_depth = np.maximum(depth_base * self.rng.uniform(0.5, 1.5, n), 1)

        vi = (ret.abs() > 0.028) & (vol_ratio > 3)

        return pd.DataFrame(
            {
                "execution_strength": exec_strength,
                "program_net_buy_krw": program,
                "foreign_net_buy_krw": foreign,
                "institution_net_buy_krw": inst,
                "spread_pct": spread_pct,
                "bid_depth_krw": bid_depth * minute_bars["close"].values,
                "ask_depth_krw": ask_depth * minute_bars["close"].values,
                "vi_triggered": vi,
            },
            index=minute_bars.index,
        )

    # ---------- MarketDataProvider interface ----------
    def get_session_timestamps(self) -> list[datetime]:
        return list(self._timestamps)

    def get_universe(self, timestamp: datetime) -> list[str]:
        return [p.symbol for p in self._symbols]

    def profile(self, symbol: str) -> SymbolProfile:
        return next(p for p in self._symbols if p.symbol == symbol)

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        minute_df = self._minute_data[symbol]
        idx = minute_df.index.get_loc(timestamp)
        mbars = minute_df.iloc[: idx + 1]
        micro = self._microstructure[symbol].iloc[idx]
        daily = self._daily_data[symbol]
        prof = self.profile(symbol)

        last_close = float(mbars["close"].iloc[-1])
        spread_pct = float(micro["spread_pct"])
        half_spread = last_close * spread_pct / 200
        bid = last_close - half_spread
        ask = last_close + half_spread

        today_val = float((mbars["close"] * mbars["volume"]).sum())
        avg20 = float(daily["trading_value"].tail(20).mean())

        index_ret = float(self._index_series.pct_change().fillna(0.0).loc[:timestamp].iloc[-1] * 100)

        admin_flags: list[str] = ["관리종목"] if prof.scenario == "thin_liquidity" and self.rng.random() < 0.0 else []

        return MarketSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            minute_bars=mbars,
            daily_bars=daily,
            bid_price=bid,
            ask_price=ask,
            bid_qty_top5=int(micro["bid_depth_krw"] / max(bid, 1)),
            ask_qty_top5=int(micro["ask_depth_krw"] / max(ask, 1)),
            execution_strength=float(micro["execution_strength"]),
            program_net_buy_krw=float(micro["program_net_buy_krw"]),
            foreign_net_buy_krw=float(micro["foreign_net_buy_krw"]),
            institution_net_buy_krw=float(micro["institution_net_buy_krw"]),
            sector_return_pct=float((mbars["close"].iloc[-1] / mbars["open"].iloc[0] - 1) * 100 * 0.6),
            theme_return_pct=float((mbars["close"].iloc[-1] / mbars["open"].iloc[0] - 1) * 100 * 0.4),
            index_return_pct=index_ret,
            vi_triggered=bool(micro["vi_triggered"]),
            admin_flags=admin_flags,
            today_trading_value_krw=today_val,
            avg_trading_value_20d_krw=avg20,
        )
