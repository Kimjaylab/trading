"""한국투자증권(KIS) Open API REST 어댑터 - 국내주식 전용.

*** 중요 ***
이 클래스는 이 개발 환경(네트워크/실계좌 접근 불가)에서 실거래 테스트를 거치지 않았다.
실사용 전 반드시 다음을 직접 검증할 것:
  1. 모의투자(virtual-trading) 도메인으로 먼저 연동 테스트 (openapivts.koreainvestment.com:29443)
  2. TR_ID, 요청/응답 필드는 KIS 공식 API 문서(개발자센터)와 대조
  3. 토큰 발급 빈도 제한(1일 1회 권장), Rate limit(초당 건수 제한) 준수
  4. 주문 수량/가격 단위(호가단위) 검증 로직 추가

이 파일은 BrokerClient 인터페이스를 만족하는 "연결 지점"을 제공하는 것이 목적이며,
전략/리스크/백테스트 로직은 이 클래스의 구현 여부와 무관하게 이미 완성되어 있다.
미국주식은 brokers/kis_overseas_broker.py를 사용할 것 (엔드포인트/TR_ID 완전히 다름).
"""
from __future__ import annotations

from datetime import datetime

import requests

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position
from trading.brokers.kis_session import KISSession


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

    # ---------- BrokerClient ----------
    def get_cash_balance(self) -> float:
        # TR_ID: 모의투자 VTTC8908R / 실전 TTTC8908R (예수금 조회) - 문서 대조 필요
        tr_id = "VTTC8908R" if self.use_virtual else "TTTC8908R"
        params = {
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
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
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
        tr_id = "VTTC8434R" if self.use_virtual else "TTTC8434R"
        params = {
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
        resp = self.session.http.get(
            f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self.session.headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

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
            resp = self.session.http.post(
                f"{self.domain}/uapi/domestic-stock/v1/trading/order-cash",
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
        # 시장가 주문 직후 체결가는 별도 체결통보/조회 API로 확인해야 한다 (여기서는 미구현).
        return OrderResult(symbol, side, quantity, price, OrderStatus.PENDING, order_id, timestamp, reason="fill_price_unconfirmed")
