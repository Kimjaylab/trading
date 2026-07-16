"""한국투자증권(KIS) Open API REST 어댑터 - 해외주식(미국 등) 전용.

*** 매우 중요 - 신뢰도 낮은 부분 명시 ***
아래 TR_ID 값(JTTT1002U 등)은 커뮤니티에 공개된 KIS 해외주식 주문 래퍼들에서
통용되는 값을 참고해 채운 것으로, 이 개발 환경(네트워크/실계좌 접근 불가)에서
KIS 공식 문서와 대조하거나 실제로 호출해보지 못했다. 국내주식 TR_ID(TTTC0802U 등)보다
확신도가 낮다. 반드시 KIS 개발자센터의 "해외주식 주문" 문서에서 다음을 직접 확인할 것:
  - TR_ID 정확한 값 (실전/모의 x 매수/매도)
  - OVRS_EXCG_CD 거래소코드 정확한 값 (나스닥/뉴욕/아멕스 구분)
  - 해외주식은 시장가 주문 지원이 거래소/시간대별로 제한적이라, 이 구현은
    항상 지정가(지정가+현재가 근사)로 주문한다 - place_order의 price 인자가
    지정가로 그대로 쓰인다.
  - 정규장 외 시간대(after-hours) 처리 여부

이 파일도 BrokerClient 인터페이스의 "연결 지점"만 제공한다. 실사용 전 모의투자
계좌로 소액 주문을 직접 넣어보고 응답 필드를 확인하는 과정이 반드시 필요하다.
"""
from __future__ import annotations

from datetime import datetime

import requests

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.brokers.kis_session import KISSession

# 확인 필요: 커뮤니티 래퍼에서 널리 쓰이는 값. KIS 공식 문서와 반드시 대조할 것.
TR_ID_BUY_REAL = "JTTT1002U"
TR_ID_SELL_REAL = "JTTT1006U"
TR_ID_BUY_VIRTUAL = "VTTT1002U"
TR_ID_SELL_VIRTUAL = "VTTT1001U"

DEFAULT_EXCHANGE = "NASD"  # NASD(나스닥)/NYSE(뉴욕)/AMEX(아멕스) - 확인 필요


class KISOverseasBroker(BrokerClient):
    def __init__(
        self,
        session: KISSession,
        account_no: str,
        account_product_code: str = "01",
        exchange_map: dict[str, str] | None = None,
        default_exchange: str = DEFAULT_EXCHANGE,
    ):
        self.session = session
        self.account_no = account_no
        self.account_product_code = account_product_code
        self.exchange_map = exchange_map or {}
        self.default_exchange = default_exchange

    @property
    def use_virtual(self) -> bool:
        return self.session.use_virtual

    @property
    def domain(self) -> str:
        return self.session.domain

    def _exchange_for(self, symbol: str) -> str:
        return self.exchange_map.get(symbol, self.default_exchange)

    # ---------- BrokerClient ----------
    def get_cash_balance(self) -> float:
        # TR_ID 확인 필요: 해외주식 예수금 조회
        tr_id = "VTTS3012R" if self.use_virtual else "TTTS3012R"
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": self.default_exchange,
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        resp = self.session.http.get(
            f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-psamount",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        return float(body["output"]["frcr_dncl_amt1"])

    def get_positions(self) -> dict[str, Position]:
        tr_id = "VTTS3012R" if self.use_virtual else "TTTS3012R"
        params = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": self.default_exchange,
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        resp = self.session.http.get(
            f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

        positions: dict[str, Position] = {}
        for row in body.get("output1", []):
            qty = int(float(row.get("ovrs_cblc_qty", 0)))
            if qty <= 0:
                continue
            symbol = row["ovrs_pdno"]
            positions[symbol] = Position(
                symbol=symbol,
                quantity=qty,
                avg_price=float(row.get("pchs_avg_pric", 0.0)),
                opened_at=datetime.now(),
                strategy="recovered_from_broker",
                stop_price=0.0,
                target_price=0.0,
            )
        return positions

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float,
        timestamp: datetime,
        strategy: str = "",
        stop_price: float = 0.0,
        target_price: float = 0.0,
    ) -> OrderResult:
        if price <= 0:
            return OrderResult(symbol, side, quantity, price, OrderStatus.REJECTED, "N/A", timestamp, reason="overseas_orders_require_limit_price")

        if side == OrderSide.BUY:
            tr_id = TR_ID_BUY_VIRTUAL if self.use_virtual else TR_ID_BUY_REAL
        else:
            tr_id = TR_ID_SELL_VIRTUAL if self.use_virtual else TR_ID_SELL_REAL

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": self._exchange_for(symbol),
            "PDNO": symbol,
            "ORD_DVSN": "00",  # 00=지정가. 해외는 거래소별로 시장가 지원이 제한적이라 지정가 고정.
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
        }
        try:
            resp = self.session.http.post(
                f"{self.domain}/uapi/overseas-stock/v1/trading/order",
                headers=self.session.headers(tr_id),
                json=body,
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            return OrderResult(symbol, side, quantity, price, OrderStatus.REJECTED, "N/A", timestamp, reason=str(exc))

        if payload.get("rt_cd") != "0":
            return OrderResult(
                symbol, side, quantity, price, OrderStatus.REJECTED,
                payload.get("output", {}).get("ODNO", "N/A"), timestamp,
                reason=payload.get("msg1", "unknown_error"),
            )

        order_id = payload["output"]["ODNO"]
        return OrderResult(symbol, side, quantity, price, OrderStatus.PENDING, order_id, timestamp, reason="fill_price_unconfirmed")
