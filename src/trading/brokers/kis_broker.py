"""한국투자증권(KIS) Open API REST 어댑터 스켈레톤.

*** 중요 ***
이 클래스는 이 개발 환경(네트워크/실계좌 접근 불가)에서 실거래 테스트를 거치지 않았다.
실사용 전 반드시 다음을 직접 검증할 것:
  1. 모의투자(virtual-trading) 도메인으로 먼저 연동 테스트 (openapivts.koreainvestment.com:29443)
  2. TR_ID, 요청/응답 필드는 KIS 공식 API 문서(개발자센터)와 대조
  3. 토큰 발급 빈도 제한(1일 1회 권장), Rate limit(초당 건수 제한) 준수
  4. 주문 수량/가격 단위(호가단위) 검증 로직 추가

이 파일은 BrokerClient 인터페이스를 만족하는 "연결 지점"을 제공하는 것이 목적이며,
전략/리스크/백테스트 로직은 이 클래스의 구현 여부와 무관하게 이미 완성되어 있다.

*** 국내(KRX) 전용이다 - 미국주식 연동은 별도 구현 필요 ***
아래 place_order()는 국내주식 현금주문 엔드포인트(/uapi/domestic-stock/v1/trading/order-cash,
TR_ID TTTC0802U 등)만 구현했다. 미국주식은 엔드포인트(/uapi/overseas-stock/v1/trading/order)와
TR_ID 체계가 완전히 다르고(매수/매도/시장 구분별 코드 상이), 주문통화·호가단위·정규장 시간
처리도 별도로 필요하다. 정확한 TR_ID는 이 환경에서 검증하지 못했으므로 KIS 공식 문서를
직접 확인해 별도 클래스(예: KISOverseasBroker)로 구현할 것을 권장한다.
"""
from __future__ import annotations

import time
from datetime import datetime

import requests

from trading.brokers.interfaces import BrokerClient, OrderResult, OrderSide, OrderStatus, Position


class KISBroker(BrokerClient):
    REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
    VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        account_product_code: str = "01",
        use_virtual: bool = True,
        session: requests.Session | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.account_product_code = account_product_code
        self.domain = self.VIRTUAL_DOMAIN if use_virtual else self.REAL_DOMAIN
        self.use_virtual = use_virtual
        self.session = session or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ---------- auth ----------
    def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        resp = self.session.post(
            f"{self.domain}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 86400))
        return self._access_token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

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
        resp = self.session.get(
            f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._headers(tr_id),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        return float(body["output2"][0]["dnca_tot_amt"])

    def get_positions(self) -> dict[str, Position]:
        raise NotImplementedError(
            "잔고 응답(output1) 파싱은 계좌 실데이터로 검증 후 구현할 것. "
            "그 전까지는 PaperBroker 또는 내부 포지션 트래커를 신뢰 소스로 사용."
        )

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
            resp = self.session.post(
                f"{self.domain}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id),
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
