"""한국투자증권(KIS) Open API REST 어댑터 - 국내주식 전용.

*** 검증 현황 (2026-07, 실사용자 모의투자 계좌로 확인) ***
- 토큰 발급, hashkey 발급: 정상 동작 확인.
- 잔고조회(TR VTTC8434R/TTTC8434R, /uapi/domestic-stock/v1/trading/inquire-balance):
  정상 동작 확인. get_cash_balance()도 이 응답의 output2[0].dnca_tot_amt를 그대로 쓴다
  (예전에 별도 TR "VTTC8908R"을 썼었는데, 그건 실제로는 "매수가능조회"용 TR이라
  PDNO/ORD_UNPR을 요구해 에러가 났다 - 예수금은 잔고조회 응답에 이미 포함되어 있다).
- place_order(주문)는 아직 실제 주문까지는 검증하지 못했다. 반드시 소액으로 먼저
  테스트하고, 주문 수량/가격 단위(호가단위) 검증 로직도 필요할 수 있다.

이 파일은 BrokerClient 인터페이스를 만족하는 "연결 지점"을 제공하는 것이 목적이며,
전략/리스크/백테스트 로직은 이 클래스의 구현 여부와 무관하게 이미 완성되어 있다.
미국주식은 brokers/kis_overseas_broker.py를 사용할 것 (엔드포인트/TR_ID 완전히 다름).

요청 쓰로틀링/재시도/토큰캐시는 KISSession이 담당한다(브로커/시세공급자 공유).
"""
from __future__ import annotations

from datetime import datetime

import requests

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.brokers.kis_session import KISAPIError, KISSession


class KISBroker(BrokerClient):
    def __init__(
        self,
        session: KISSession,
        account_no: str,
        account_product_code: str = "01",
    ):
        self.session = session
        self.account_no = account_no
        self.account_product_code = account_product_code

    @property
    def use_virtual(self) -> bool:
        return self.session.use_virtual

    @property
    def domain(self) -> str:
        return self.session.domain

    @staticmethod
    def _raise_if_error(body: dict) -> None:
        if body.get("rt_cd") != "0":
            raise KISAPIError(f"{body.get('msg_cd', '')} {body.get('msg1', 'unknown_error')}".strip())

    def _balance_params(self) -> dict:
        return {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

    def _fetch_balance(self) -> dict:
        """국내주식 잔고조회(TTTC8434R/VTTC8434R) - 예수금(output2)과 보유종목(output1)을
        한 번의 호출로 함께 내려준다. 실사용자 계좌로 검증 완료(2026-07)."""
        tr_id = "VTTC8434R" if self.use_virtual else "TTTC8434R"
        resp = self.session.request(
            "GET",
            f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self.session.headers(tr_id),
            params=self._balance_params(),
        )
        body = resp.json()
        self._raise_if_error(body)
        return body

    # ---------- BrokerClient ----------
    def get_cash_balance(self) -> float:
        body = self._fetch_balance()
        return float(body["output2"][0]["dnca_tot_amt"])

    def get_positions(self) -> dict[str, Position]:
        """계좌 잔고(output1)를 Position으로 변환한다.

        *** 주의 ***: KIS 잔고조회 응답에는 진입시각/전략명/손절가/목표가가 없다.
        이 필드들은 원래 이 시스템의 RiskManager/전략이 진입 시점에 부여하는 값이라,
        브로커 재시작 등으로 내부 상태(트래킹)를 잃은 뒤 이 메서드로 복구하면
        stop/target이 비어있는 상태(0.0)로 채워진다 - 실거래 루프는 내부 포지션
        트래커(RiskManager/BacktestEngine._entry_context)를 1차 소스로 쓰고,
        이 메서드는 장애 복구/검증용 보조 수단으로만 사용할 것을 권장한다.
        """
        body = self._fetch_balance()

        positions: dict[str, Position] = {}
        for row in body.get("output1", []):
            qty = int(row.get("hldg_qty", 0))
            if qty <= 0:
                continue
            symbol = row["pdno"]
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
        # TR_ID: 모의 매수 VTTC0802U / 매도 VTTC0801U, 실전 매수 TTTC0802U / 매도 TTTC0801U
        if side == OrderSide.BUY:
            tr_id = "VTTC0802U" if self.use_virtual else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if self.use_virtual else "TTTC0801U"

        body = {
            "CANO": self.account_no,
            "ACNT_PRDT_CD": self.account_product_code,
            "PDNO": symbol,
            "ORD_DVSN": "01",  # 01=시장가. 지정가는 00 + ORD_UNPR 필요
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        }
        try:
            hashkey = self.session.get_hashkey(body)
            resp = self.session.request(
                "POST",
                f"{self.domain}/uapi/domestic-stock/v1/trading/order-cash",
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
        # 시장가 주문 직후 실제 체결가는 별도 체결통보/조회 API로 확인해야 하는데 아직
        # 구현하지 않았다. 다만 engine.py(_process_entries/_execute_exit)는 OrderStatus.FILLED만
        # 성공으로 인식해 리스크매니저/트레이드기록을 갱신하므로, 여기서 PENDING을 반환하면
        # 실제로는 주문이 접수/체결됐어도 내부적으로 영원히 "실패"로 취급되어 손절/익절이
        # 동작하지 않는 치명적 버그가 된다. 요청 시 넘긴 가격을 근사 체결가로 간주해 FILLED로
        # 처리한다 - 시장가 주문이라 실제 체결가와 다를 수 있으니 추후 체결통보 API 연동 시 개선할 것.
        return OrderResult(symbol, side, quantity, price, OrderStatus.FILLED, order_id, timestamp, reason="fill_price_unconfirmed")
