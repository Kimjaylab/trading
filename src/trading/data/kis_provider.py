"""한국투자증권(KIS) Open API 기반 실시간 국내주식 시세 공급자.

*** 신뢰도 안내 ***
이 개발 환경은 네트워크/실계좌 접근이 불가능해 아래 엔드포인트를 실제로 호출해보지
못했다. TR_ID와 응답 필드명은 공개된 KIS 개발자문서/커뮤니티 래퍼를 참고해 채웠으며,
아래 표의 확신도 순서대로 검증 우선순위를 두는 것을 권장한다:

  1. (확신 높음) 현재가/호가/분봉/일봉 조회 - 가장 널리 쓰이고 문서화가 잘 된 엔드포인트.
  2. (미구현) 프로그램매매·외국인/기관 수급·VI 발동·섹터/테마 강도 - 각각 별도 엔드포인트가
     필요하고 이 환경에서 확인하지 못해 중립값(0 또는 False)으로 채운다. 이 피처들의
     스코어링 기여도가 사실상 사라지므로, 실사용 시 config/weights.json에서 해당 피처
     가중치를 낮추거나(혹은 ML 재학습으로 자연히 낮아짐) 데이터 소스를 추가 연동할 것.

get_universe()는 KIS의 '거래량/거래대금 순위' 조회 API를 추정 구현하는 대신, 생성자에
전달받은 고정 종목 리스트(watchlist)를 그대로 반환한다 - 순위 조회 TR_ID를 검증하지
못한 채로 자동 스크리닝을 흉내내는 것보다, 사용자가 직접 관리하는 관심종목 리스트를
쓰는 편이 훨씬 안전하다고 판단했다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import requests

from trading.brokers.kis_session import KISSession
from trading.data.interfaces import MarketDataProvider, MarketSnapshot


class KISMarketDataProvider(MarketDataProvider):
    def __init__(self, session: KISSession, watchlist: list[str]):
        self.session = session
        self.watchlist = list(watchlist)
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._daily_cache_date: dict[str, str] = {}

    @property
    def domain(self) -> str:
        return self.session.domain

    # ---------- MarketDataProvider ----------
    def get_universe(self, timestamp: datetime) -> list[str]:
        return list(self.watchlist)

    def get_session_timestamps(self) -> list[datetime]:
        raise NotImplementedError(
            "실시간 공급자는 미리 정해진 타임스탬프 목록이 없다. "
            "execution.live_runner.LiveRunner가 실제 시계를 기준으로 매 분 step()을 호출한다."
        )

    def get_snapshot(self, symbol: str, timestamp: datetime) -> MarketSnapshot:
        price_info = self._fetch_price(symbol)
        bid, ask, bid_qty, ask_qty = self._fetch_orderbook(symbol)
        minute_bars = self._fetch_minute_bars(symbol)
        daily_bars = self._fetch_daily_bars(symbol)

        today_value = float((minute_bars["close"] * minute_bars["volume"]).sum())
        avg20 = float(daily_bars["trading_value"].tail(20).mean()) if not daily_bars.empty else today_value

        return MarketSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            minute_bars=minute_bars,
            daily_bars=daily_bars,
            bid_price=bid,
            ask_price=ask,
            bid_qty_top5=bid_qty,
            ask_qty_top5=ask_qty,
            execution_strength=price_info.get("execution_strength", 100.0),
            program_net_buy_krw=0.0,  # TODO: 프로그램매매 동향 API 연동 필요
            foreign_net_buy_krw=0.0,  # TODO: 외국인 수급 API 연동 필요
            institution_net_buy_krw=0.0,  # TODO: 기관 수급 API 연동 필요
            sector_return_pct=0.0,  # TODO: 업종지수 API 연동 필요
            theme_return_pct=0.0,  # TODO: 테마 데이터 소스 없음 (KIS 표준 API에 없음)
            index_return_pct=0.0,  # TODO: 코스피/코스닥 지수 API 연동 필요
            vi_triggered=False,  # TODO: VI 발동 여부 API 연동 필요
            admin_flags=[],  # TODO: 관리종목/투자경고 여부는 종목마스터 정보 연동 필요
            today_trading_value_krw=today_value,
            avg_trading_value_20d_krw=avg20,
        )

    # ---------- KIS REST calls (미검증 - 실사용 전 문서 대조 필수) ----------
    def _fetch_price(self, symbol: str) -> dict:
        tr_id = "FHKST01010100"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        output = resp.json().get("output", {})
        return {
            "price": float(output.get("stck_prpr", 0.0)),
            # 체결강도는 응답에 없을 수 있음 - 있으면 사용, 없으면 중립값(100) 사용
            "execution_strength": float(output.get("chgrt", 100.0)) if output.get("chgrt") else 100.0,
        }

    def _fetch_orderbook(self, symbol: str) -> tuple[float, float, int, int]:
        tr_id = "FHKST01010200"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        output = resp.json().get("output1", {})
        bid = float(output.get("bidp1", 0.0))
        ask = float(output.get("askp1", 0.0))
        bid_qty = int(float(output.get("bidp_rsqn1", 0)))
        ask_qty = int(float(output.get("askp_rsqn1", 0)))
        return bid, ask, bid_qty, ask_qty

    def _fetch_minute_bars(self, symbol: str) -> pd.DataFrame:
        tr_id = "FHKST03010200"
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": "",
            "FID_PW_DATA_INCU_YN": "Y",
        }
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("output2", [])
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        records = []
        for r in reversed(rows):  # KIS는 보통 최신순으로 내려주므로 시간순 정렬
            ts = datetime.strptime(f"{r['stck_bsop_date']}{r['stck_cntg_hour']}", "%Y%m%d%H%M%S")
            records.append(
                {
                    "timestamp": ts,
                    "open": float(r["stck_oprc"]),
                    "high": float(r["stck_hgpr"]),
                    "low": float(r["stck_lwpr"]),
                    "close": float(r["stck_prpr"]),
                    "volume": float(r["cntg_vol"]),
                }
            )
        return pd.DataFrame(records).set_index("timestamp")

    def _fetch_daily_bars(self, symbol: str, lookback_days: int = 30) -> pd.DataFrame:
        today_str = datetime.now().strftime("%Y%m%d")
        if self._daily_cache_date.get(symbol) == today_str:
            return self._daily_cache[symbol]

        tr_id = "FHKST03010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_DATE_2": today_str,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("output2", [])[:lookback_days]

        records = []
        for r in reversed(rows):
            date = datetime.strptime(r["stck_bsop_date"], "%Y%m%d")
            close = float(r["stck_clpr"])
            volume = float(r["acml_vol"])
            records.append(
                {
                    "date": date,
                    "open": float(r["stck_oprc"]),
                    "high": float(r["stck_hgpr"]),
                    "low": float(r["stck_lwpr"]),
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
