"""한국투자증권(KIS) Open API 기반 실시간 해외주식(미국 등) 시세 공급자.

*** 신뢰도 안내 (국내용 kis_provider.py보다 더 낮음) ***
현재가/일봉 엔드포인트(TR_CURRENT_PRICE=HHDFS00000300, TR_DAILY_PRICE=HHDFS76240000)는
같은 계정(kimjaylab)의 `claude` 저장소 `claude/ai-trading-bot-kiwoom-kagv8a` 브랜치의
별도 프로젝트(trading_bot/kis_client.py)를 참고해 가져왔다 - 그 코드는 KIS 공식 GitHub
레퍼런스를 근거로 작성되었다고 명시되어 있어 이 프로젝트가 처음부터 추측한 값보다 신뢰도가
높다. 다만 응답 필드명(output2 각 행의 실제 키)까지는 그 프로젝트도 파고들지 않았으므로
(일봉 데이터를 DataFrame으로 그대로 흘려보내는 방식이라 키 이름에 의존하지 않았음),
아래 _parse_daily_row는 여러 후보 키를 시도하는 방어적 파싱을 쓴다 - 여전히 실거래
투입 전 실제 응답으로 검증 필요.

*** 결정적 한계: 분봉(장중 인트라데이) 데이터가 없다 ***
KIS 해외주식 시세 API는 일봉/현재가만 확인했고 미국주식 분봉 조회 엔드포인트는
찾지 못했다. 그래서 이 공급자는 폴링할 때마다(LiveRunner의 poll_interval마다) 받은
현재가를 그 시점의 1개 '분봉'으로 취급해 메모리에 누적한다 - 실제 그 폴링 간격
동안의 고가/저가 변동은 반영되지 못하고 현재가 하나로 단순화된다. 폴링 주기를
짧게(예: 60초) 잡을수록 근사 오차가 줄어들지만, 진짜 분봉 데이터는 아니다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading.brokers.kis_exchange_codes import DEFAULT_EXCHANGE, QUOTE_EXCHANGE_CODES
from trading.brokers.kis_session import KISSession
from trading.data.interfaces import MarketDataProvider, MarketSnapshot

TR_CURRENT_PRICE = "HHDFS00000300"
TR_DAILY_PRICE = "HHDFS76240000"


def _first_present(row: dict, keys: list[str], default: float = 0.0) -> float:
    for key in keys:
        if key in row and row[key] not in ("", None):
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return default


class KISOverseasMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        session: KISSession,
        watchlist: list[str],
        exchange_map: dict[str, str] | None = None,
        usd_krw_rate: float = 1450.0,
    ):
        # MarketSnapshot의 거래대금 필드는 이름 그대로 원화(KRW) 기준이어야 필터(filters/exclusion.py)의
        # KRW 절대치 임계값과 비교가 맞는다. 해외주식은 원시 값이 USD라서 그대로 채우면 약 1300~1500배
        # 작게 계산되어 사실상 모든 종목이 유동성 부족으로 걸린다 - 대략적인 환율로 KRW 환산해 채운다.
        # 실시간 환율 조회 API는 아직 연동하지 않았으므로 근사치이며, --usd-krw-rate로 조정 가능하다.
        self.session = session
        self.watchlist = list(watchlist)
        self.exchange_map = exchange_map or {}
        self.usd_krw_rate = usd_krw_rate
        self._minute_buffer: dict[str, pd.DataFrame] = {}
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._daily_cache_date: dict[str, str] = {}

    @property
    def domain(self) -> str:
        return self.session.domain

    def _exchange_name_for(self, symbol: str) -> str:
        return self.exchange_map.get(symbol, DEFAULT_EXCHANGE)

    def _quote_exchange_code_for(self, symbol: str) -> str:
        return QUOTE_EXCHANGE_CODES[self._exchange_name_for(symbol)]

    # ---------- MarketDataProvider ----------
    def get_universe(self, timestamp: datetime) -> list[str]:
        return list(self.watchlist)

    def get_session_timestamps(self) -> list[datetime]:
        raise NotImplementedError(
            "실시간 공급자는 미리 정해진 타임스탬프 목록이 없다. "
            "execution.live_runner.LiveRunner가 실제 시계 기준으로 매 폴링마다 step()을 호출한다."
        )

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        price_info = self._fetch_current_price(symbol)
        self._append_minute_bar(symbol, timestamp, price_info)
        minute_bars = self._minute_buffer[symbol]
        daily_bars = self._fetch_daily_bars(symbol)

        today_value_usd = float((minute_bars["close"] * minute_bars["volume"]).sum())
        avg20_usd = float(daily_bars["trading_value"].tail(20).mean()) if not daily_bars.empty else today_value_usd
        today_value = today_value_usd * self.usd_krw_rate
        avg20 = avg20_usd * self.usd_krw_rate

        last_price = price_info["price"]
        spread_guess = max(last_price * 0.0005, 0.01)  # 해외는 호가창 조회 미구현 - 최소 스프레드로 근사

        return MarketSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            minute_bars=minute_bars,
            daily_bars=daily_bars,
            bid_price=last_price - spread_guess / 2,
            ask_price=last_price + spread_guess / 2,
            bid_qty_top5=0,  # TODO: 해외주식 호가 조회 엔드포인트 미구현
            ask_qty_top5=0,
            execution_strength=100.0,  # TODO: 해외주식은 체결강도 개념/데이터가 없음 - 중립값
            program_net_buy_krw=0.0,  # TODO: 국내 전용 개념, 해외주식에는 해당 없음
            foreign_net_buy_krw=0.0,
            institution_net_buy_krw=0.0,
            sector_return_pct=0.0,  # TODO: 미국 섹터지수 API 연동 필요
            theme_return_pct=0.0,
            index_return_pct=0.0,  # TODO: S&P500/나스닥 지수 API 연동 필요
            vi_triggered=False,  # 미국은 KRX식 VI가 아니라 LULD 밴드 - 별도 연동 필요
            admin_flags=[],
            today_trading_value_krw=today_value,
            avg_trading_value_20d_krw=avg20,
        )

    # ---------- 내부 ----------
    def _append_minute_bar(self, symbol: str, timestamp: datetime, price_info: dict) -> None:
        price = price_info["price"]
        volume = price_info.get("volume", 0.0)
        buf = self._minute_buffer.get(symbol)
        prev_close = float(buf["close"].iloc[-1]) if buf is not None and len(buf) else price
        prev_cum_volume = float(buf.attrs.get("cum_volume", 0.0)) if buf is not None else 0.0
        bar_volume = max(volume - prev_cum_volume, 0.0)

        row = pd.DataFrame(
            {"open": [prev_close], "high": [max(prev_close, price)], "low": [min(prev_close, price)], "close": [price], "volume": [bar_volume]},
            index=[timestamp],
        )
        buf = pd.concat([buf, row]) if buf is not None else row
        buf.attrs["cum_volume"] = volume
        self._minute_buffer[symbol] = buf

    def _fetch_current_price(self, symbol: str) -> dict:
        resp = self.session.request(
            "GET",
            f"{self.domain}/uapi/overseas-price/v1/quotations/price-detail",
            headers=self.session.headers(TR_CURRENT_PRICE),
            params={"AUTH": "", "EXCD": self._quote_exchange_code_for(symbol), "SYMB": symbol},
        )
        output = resp.json().get("output", {})
        return {
            "price": _first_present(output, ["last", "base", "stck_prpr"]),
            "volume": _first_present(output, ["tvol", "acml_vol"]),
        }

    def _fetch_daily_bars(self, symbol: str, lookback_days: int = 30) -> pd.DataFrame:
        today_str = datetime.now().strftime("%Y%m%d")
        if self._daily_cache_date.get(symbol) == today_str:
            return self._daily_cache[symbol]

        resp = self.session.request(
            "GET",
            f"{self.domain}/uapi/overseas-price/v1/quotations/dailyprice",
            headers=self.session.headers(TR_DAILY_PRICE),
            params={
                "AUTH": "",
                "EXCD": self._quote_exchange_code_for(symbol),
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": "",
                "MODP": "0",
            },
        )
        rows = resp.json().get("output2", [])[:lookback_days]

        records = []
        for row in reversed(rows):
            date_str = None
            for key in ("xymd", "stck_bsop_date", "zdiv"):
                if key in row and row[key]:
                    date_str = row[key]
                    break
            if not date_str or len(str(date_str)) != 8:
                continue
            close = _first_present(row, ["clos", "close", "stck_clpr"])
            volume = _first_present(row, ["tvol", "acml_vol", "volume"])
            records.append(
                {
                    "date": datetime.strptime(str(date_str), "%Y%m%d"),
                    "open": _first_present(row, ["open", "stck_oprc"], close),
                    "high": _first_present(row, ["high", "stck_hgpr"], close),
                    "low": _first_present(row, ["low", "stck_lwpr"], close),
                    "close": close,
                    "volume": volume,
                    "trading_value": close * volume,
                }
            )
        df = pd.DataFrame(records).set_index("date") if records else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume", "trading_value"]
        )
        self._daily_cache[symbol] = df
        self._daily_cache_date[symbol] = today_str
        return df
