"""한국투자증권(KIS) Open API REST 어댑터 - 해외주식(미국 등) 전용.

TR_ID는 같은 계정(kimjaylab)의 `claude` 저장소 `claude/ai-trading-bot-kiwoom-kagv8a`
브랜치에 있던 별도 프로젝트(trading_bot/kis_client.py, KIS 공식 GitHub 레퍼런스를
근거로 작성됨)를 참고해 정정했다: 매수/매도 실전 TR_ID에 "T"로 시작하는 값
(TTTT1002U/TTTT1006U)을 쓰고, 모의투자는 첫 글자를 "V"로 바꾼 값(VTTT1002U/VTTT1006U)을
쓴다 - 이전 버전에서 썼던 JTTT.../VTTT1001U 조합보다 이 쪽이 국내주식 TR_ID 패턴
(TTTC.../VTTC...)과 일관되어 신뢰도가 더 높다고 판단했다.

*** 그래도 여전히 확인이 필요하다 ***: 이 환경은 네트워크/실계좌 접근이 불가능해
실제로 호출해 검증하지 못했다. 실사용 전 KIS 개발자센터의 "해외주식 주문" 문서에서
반드시 대조할 것. 특히:
  - OVRS_EXCG_CD 거래소코드 (brokers/kis_exchange_codes.py 참고 - trading/quotations
    API가 서로 다른 코드 체계를 쓴다고 알려져 있음, 확인 필요)
  - 해외주식은 시장가 주문 지원이 거래소/시간대별로 제한적이라, 이 구현은
    항상 지정가로 주문한다 (place_order의 price 인자가 지정가로 그대로 쓰인다)
  - 정규장 외 시간대(after-hours) 처리 여부

이 파일도 BrokerClient 인터페이스의 "연결 지점"만 제공한다. 실사용 전 모의투자
계좌로 소액 주문을 직접 넣어보고 응답 필드를 확인하는 과정이 반드시 필요하다.
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.brokers.kis_exchange_codes import DEFAULT_EXCHANGE, TRADING_EXCHANGE_CODES
from trading.brokers.kis_session import KISAPIError, KISSession

logger = logging.getLogger(__name__)

# 실사용자 계좌로 확인한 결과(2026-07), inquire-balance 응답의 output2에는 이 후보들 중
# 어느 것도 명확한 "외화 예수금 총액"으로 보이지 않았다 (P&L/매입금액 위주 필드만 존재).
# 정확한 필드/엔드포인트를 찾을 때까지, 여기 없는 키는 0.0으로 안전하게 처리한다.
_CASH_FIELD_CANDIDATES = ("frcr_dncl_amt1", "frcr_dncl_amt_2", "dncl_amt")

TR_ID_BUY_REAL = "TTTT1002U"
TR_ID_SELL_REAL = "TTTT1006U"


def _virtual_tr_id(real_tr_id: str) -> str:
    return "V" + real_tr_id[1:]


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

    def _exchange_name_for(self, symbol: str) -> str:
        return self.exchange_map.get(symbol, self.default_exchange)

    def _trading_exchange_code_for(self, symbol: str) -> str:
        return TRADING_EXCHANGE_CODES[self._exchange_name_for(symbol)]

    @staticmethod
    def _raise_if_error(body: dict) -> None:
        if body.get("rt_cd") != "0":
            raise KISAPIError(f"{body.get('msg_cd', '')} {body.get('msg1', 'unknown_error')}".strip())

    def _balance_params(self) -> dict:
        return {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": TRADING_EXCHANGE_CODES[self.default_exchange],
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

    def _fetch_balance(self) -> dict:
        """해외주식 잔고조회(TTTS3012R/VTTS3012R). 실사용자 계좌로 호출 자체는 확인했으나
        (rt_cd=0 응답 받음), output2에서 외화 예수금 총액에 해당하는 필드를 아직 특정하지
        못했다 - get_cash_balance()는 이 응답에서 후보 필드를 찾고, 없으면 0.0을 반환한다."""
        tr_id = "VTTS3012R" if self.use_virtual else "TTTS3012R"
        resp = self.session.request(
            "GET",
            f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=self.session.headers(tr_id),
            params=self._balance_params(),
        )
        body = resp.json()
        self._raise_if_error(body)
        return body

    # ---------- BrokerClient ----------
    def get_cash_balance(self) -> float:
        body = self._fetch_balance()
        output2 = body.get("output2", {})
        if isinstance(output2, list):
            output2 = output2[0] if output2 else {}
        for key in _CASH_FIELD_CANDIDATES:
            if key in output2:
                return float(output2[key])
        logger.warning(
            "해외주식 예수금 필드를 응답에서 찾지 못했다 (후보: %s). 0.0을 반환한다 - "
            "실제 예수금은 KIS 앱에서 직접 확인할 것. 응답 output2 키: %s",
            _CASH_FIELD_CANDIDATES, list(output2.keys()),
        )
        return 0.0

    def get_positions(self) -> dict[str, Position]:
        body = self._fetch_balance()

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

        real_tr_id = TR_ID_BUY_REAL if side == OrderSide.BUY else TR_ID_SELL_REAL
        tr_id = _virtual_tr_id(real_tr_id) if self.use_virtual else real_tr_id

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "OVRS_EXCG_CD": self._trading_exchange_code_for(symbol),
            "PDNO": symbol,
            "ORD_DVSN": "00",  # 00=지정가. 해외는 거래소별로 시장가 지원이 제한적이라 지정가 고정.
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": f"{price:.2f}",
            "ORD_SVR_DVSN_CD": "0",
        }
        try:
            hashkey = self.session.get_hashkey(body)
            resp = self.session.request(
                "POST",
                f"{self.domain}/uapi/overseas-stock/v1/trading/order",
                headers=self.session.headers(tr_id, extra={"hashkey": hashkey}),
                json_body=body,
            )
            payload = resp.json()
        except (requests.RequestException, KISAPIError) as exc:
            return OrderResult(symbol, side, quantity, price, OrderStatus.REJECTED, "N/A", timestamp, reason=str(exc))

        if payload.get("rt_cd") != "0":
            return OrderResult(
                symbol, side, quantity, price, OrderStatus.REJECTED,
                payload.get("output", {}).get("ODNO", "N/A"), timestamp,
                reason=payload.get("msg1", "unknown_error"),
            )

        order_id = payload["output"]["ODNO"]
        return OrderResult(symbol, side, quantity, price, OrderStatus.PENDING, order_id, timestamp, reason="fill_price_unconfirmed")
