"""실제 과거 데이터(CSV)를 MarketDataProvider 인터페이스로 공급하는 어댑터.

기대하는 디렉토리 구조:
    data_dir/
      minute/{symbol}.csv   # timestamp, open, high, low, close, volume, [execution_strength, program_net_buy_krw,
                            #  foreign_net_buy_krw, institution_net_buy_krw, bid_price, ask_price,
                            #  bid_qty_top5, ask_qty_top5, vi_triggered]
      daily/{symbol}.csv    # date, open, high, low, close, volume, trading_value
      index.csv             # timestamp, close   (코스피/코스닥 등 지수)
      universe.csv          # symbol, sector, theme, admin_flags(선택, ';'로 구분)

미시구조 컬럼이 없으면 합리적인 기본값/추정치로 채운다 (호가는 종가 기준 스프레드 가정 등).
실거래 대비 정확도는 떨어지지만, 조건별 백테스트 로직 자체는 동일하게 검증할 수 있다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from trading.data.interfaces import MarketDataProvider, MarketSnapshot


class CSVDataProvider(MarketDataProvider):
    def __init__(self, data_dir: Path | str, default_spread_pct: float = 0.15):
        self.data_dir = Path(data_dir)
        self.default_spread_pct = default_spread_pct

        universe_path = self.data_dir / "universe.csv"
        self._universe_meta = pd.read_csv(universe_path, dtype=str).set_index("symbol") if universe_path.exists() else pd.DataFrame()

        self._minute: dict[str, pd.DataFrame] = {}
        self._daily: dict[str, pd.DataFrame] = {}
        for p in sorted((self.data_dir / "minute").glob("*.csv")):
            symbol = p.stem
            df = pd.read_csv(p, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            self._minute[symbol] = df
        for p in sorted((self.data_dir / "daily").glob("*.csv")):
            symbol = p.stem
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
            self._daily[symbol] = df

        index_path = self.data_dir / "index.csv"
        if index_path.exists():
            idx_df = pd.read_csv(index_path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            self._index_close = idx_df["close"]
        else:
            self._index_close = None

        common_ts: set[datetime] | None = None
        for df in self._minute.values():
            ts_set = set(df.index.to_pydatetime())
            common_ts = ts_set if common_ts is None else (common_ts & ts_set)
        self._timestamps = sorted(common_ts or set())

    def get_session_timestamps(self) -> list[datetime]:
        return list(self._timestamps)

    def get_universe(self, timestamp: datetime) -> list[str]:
        return list(self._minute.keys())

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        minute_df = self._minute[symbol]
        mbars = minute_df.loc[:timestamp]
        daily = self._daily.get(symbol, pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trading_value"]))
        row = mbars.iloc[-1]

        last_close = float(row["close"])
        spread_pct = float(row["spread_pct"]) if "spread_pct" in row else self.default_spread_pct
        half_spread = last_close * spread_pct / 200
        bid = float(row["bid_price"]) if "bid_price" in row else last_close - half_spread
        ask = float(row["ask_price"]) if "ask_price" in row else last_close + half_spread

        today_val = float((mbars["close"] * mbars["volume"]).sum())
        avg20 = float(daily["trading_value"].tail(20).mean()) if not daily.empty else today_val

        index_ret = 0.0
        if self._index_close is not None:
            idx_slice = self._index_close.loc[:timestamp]
            if len(idx_slice) >= 2:
                index_ret = float(idx_slice.pct_change().iloc[-1] * 100)

        meta = self._universe_meta.loc[symbol] if symbol in getattr(self._universe_meta, "index", []) else None
        sector = meta["sector"] if meta is not None and "sector" in meta else "UNKNOWN"
        theme = meta["theme"] if meta is not None and "theme" in meta else "UNKNOWN"
        admin_flags = []
        if meta is not None and "admin_flags" in meta and isinstance(meta["admin_flags"], str) and meta["admin_flags"]:
            admin_flags = meta["admin_flags"].split(";")

        return MarketSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            minute_bars=mbars,
            daily_bars=daily,
            bid_price=bid,
            ask_price=ask,
            bid_qty_top5=int(row["bid_qty_top5"]) if "bid_qty_top5" in row else 0,
            ask_qty_top5=int(row["ask_qty_top5"]) if "ask_qty_top5" in row else 0,
            execution_strength=float(row["execution_strength"]) if "execution_strength" in row else 100.0,
            program_net_buy_krw=float(row["program_net_buy_krw"]) if "program_net_buy_krw" in row else 0.0,
            foreign_net_buy_krw=float(row["foreign_net_buy_krw"]) if "foreign_net_buy_krw" in row else 0.0,
            institution_net_buy_krw=float(row["institution_net_buy_krw"]) if "institution_net_buy_krw" in row else 0.0,
            sector_return_pct=0.0,
            theme_return_pct=0.0,
            index_return_pct=index_ret,
            vi_triggered=bool(row["vi_triggered"]) if "vi_triggered" in row else False,
            admin_flags=admin_flags,
            today_trading_value_krw=today_val,
            avg_trading_value_20d_krw=avg20,
        )
