"""KIS 브로커 어댑터의 요청 구성/응답 파싱 로직을 네트워크 없이 검증한다.

실제 KIS 서버 응답 스펙과 100% 동일하다는 보장은 없지만(이 환경은 네트워크 접근이
없어 실제 호출로 검증 불가), 최소한 이 코드가 "의도한 대로" 요청을 만들고
응답을 파싱하는지는 가짜(fake) HTTP 세션으로 확인할 수 있다.
"""
from __future__ import annotations

import tempfile
from datetime import datetime

import pytest

from trading.brokers.interfaces import OrderSide, OrderStatus
from trading.brokers.kis_broker import KISBroker
from trading.brokers.kis_overseas_broker import KISOverseasBroker
from trading.brokers.kis_session import KISAPIError, KISSession


class FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json


class FakeHTTP:
    """requests.Session을 대체하는 가짜 세션. 호출 기록을 남겨 검증에 사용한다."""

    def __init__(self):
        self.calls: list[dict] = []
        self.token_call_count = 0
        self.next_responses: dict[str, FakeResponse] = {}

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "json": json, "params": params, "headers": headers})
        if "/oauth2/tokenP" in url:
            self.token_call_count += 1
            return FakeResponse({"access_token": "fake-token", "expires_in": 86400})
        if "/uapi/hashkey" in url:
            return FakeResponse({"HASH": "fake-hash"})
        return self.next_responses.get(url, FakeResponse({"rt_cd": "0", "output": {"ODNO": "0000001"}}))


def _session(use_virtual=True) -> tuple[KISSession, FakeHTTP]:
    fake = FakeHTTP()
    # 매 테스트마다 고유한(존재하지 않는) 캐시 경로를 써서 테스트 간 토큰 캐시 오염을 막는다.
    session = KISSession(
        "appkey", "appsecret", use_virtual=use_virtual, http=fake,
        request_interval_sec=0.0, token_cache_path=tempfile.mktemp(suffix=".json"),
    )
    return session, fake


def test_token_is_cached_across_multiple_calls():
    session, fake = _session()
    session.ensure_token()
    session.ensure_token()
    session.ensure_token()
    assert fake.token_call_count == 1


def test_hashkey_is_requested_before_domestic_order():
    session, fake = _session(use_virtual=True)
    broker = KISBroker(session, account_no="12345678", account_product_code="01")

    broker.place_order("005930", OrderSide.BUY, 10, 70000, datetime.now())

    assert any("/uapi/hashkey" in c["url"] for c in fake.calls)


def test_domestic_place_order_uses_virtual_tr_id_and_market_order():
    session, fake = _session(use_virtual=True)
    broker = KISBroker(session, account_no="12345678", account_product_code="01")

    result = broker.place_order("005930", OrderSide.BUY, 10, 70000, datetime.now())

    assert result.status == OrderStatus.PENDING
    order_call = next(c for c in fake.calls if "order-cash" in c["url"])
    assert order_call["headers"]["tr_id"] == "VTTC0802U"
    assert order_call["json"]["PDNO"] == "005930"
    assert order_call["json"]["ORD_QTY"] == "10"


def test_domestic_place_order_uses_real_tr_id_for_sell():
    session, fake = _session(use_virtual=False)
    broker = KISBroker(session, account_no="12345678")

    broker.place_order("005930", OrderSide.SELL, 5, 70000, datetime.now())

    order_call = next(c for c in fake.calls if "order-cash" in c["url"])
    assert order_call["headers"]["tr_id"] == "TTTC0801U"


def test_domestic_place_order_rejected_when_rt_cd_not_zero():
    session, fake = _session()
    url = f"{session.domain}/uapi/domestic-stock/v1/trading/order-cash"
    fake.next_responses[url] = FakeResponse({"rt_cd": "1", "msg1": "잔고부족", "output": {}})
    broker = KISBroker(session, account_no="12345678")

    result = broker.place_order("005930", OrderSide.BUY, 10, 70000, datetime.now())

    assert result.status == OrderStatus.REJECTED
    assert result.reason == "잔고부족"


def test_domestic_get_positions_parses_and_filters_zero_qty():
    session, fake = _session()
    url = f"{session.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {
            "rt_cd": "0",
            "output1": [
                {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70000"},
                {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "150000"},
            ],
            "output2": [{"dnca_tot_amt": "5000000"}],
        }
    )
    broker = KISBroker(session, account_no="12345678")

    positions = broker.get_positions()

    assert set(positions.keys()) == {"005930"}
    assert positions["005930"].quantity == 10
    assert positions["005930"].avg_price == 70000.0


def test_domestic_get_cash_balance_raises_clear_error_on_invalid_account():
    session, fake = _session()
    url = f"{session.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {"rt_cd": "2", "msg_cd": "OPSQ2000", "msg1": "ERROR : INPUT INVALID_CHECK_ACNO"}
    )
    broker = KISBroker(session, account_no="bad-account")

    with pytest.raises(KISAPIError, match="INVALID_CHECK_ACNO"):
        broker.get_cash_balance()


def test_domestic_get_positions_raises_clear_error_instead_of_silently_empty():
    """get_positions()가 에러 응답을 '포지션 없음'으로 잘못 삼키지 않는지 확인한다."""
    session, fake = _session()
    url = f"{session.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {"rt_cd": "2", "msg_cd": "OPSQ2000", "msg1": "ERROR : INPUT INVALID_CHECK_ACNO"}
    )
    broker = KISBroker(session, account_no="bad-account")

    with pytest.raises(KISAPIError, match="INVALID_CHECK_ACNO"):
        broker.get_positions()


def test_overseas_get_cash_balance_returns_zero_when_field_unavailable():
    """실사용자 계좌(2026-07)에서 확인된 실제 응답 모양: output2에 예수금 필드가 없다."""
    session, fake = _session()
    url = f"{session.domain}/uapi/overseas-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {
            "rt_cd": "0",
            "output1": [],
            "output2": {"frcr_pchs_amt1": "0.00000", "tot_evlu_pfls_amt": "0.00000000"},
        }
    )
    broker = KISOverseasBroker(session, account_no="12345678")

    assert broker.get_cash_balance() == 0.0


def test_overseas_get_cash_balance_uses_candidate_field_when_present():
    session, fake = _session()
    url = f"{session.domain}/uapi/overseas-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {"rt_cd": "0", "output1": [], "output2": {"frcr_dncl_amt1": "1234.56"}}
    )
    broker = KISOverseasBroker(session, account_no="12345678")

    assert broker.get_cash_balance() == 1234.56


def test_overseas_get_positions_parses_output1():
    session, fake = _session()
    url = f"{session.domain}/uapi/overseas-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse(
        {
            "rt_cd": "0",
            "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3", "pchs_avg_pric": "150.5"}],
            "output2": {},
        }
    )
    broker = KISOverseasBroker(session, account_no="12345678")

    positions = broker.get_positions()

    assert positions["AAPL"].quantity == 3
    assert positions["AAPL"].avg_price == 150.5


def test_overseas_place_order_requires_limit_price():
    session, _ = _session()
    broker = KISOverseasBroker(session, account_no="12345678")

    result = broker.place_order("AAPL", OrderSide.BUY, 5, 0.0, datetime.now())

    assert result.status == OrderStatus.REJECTED
    assert result.reason == "overseas_orders_require_limit_price"


def test_overseas_place_order_uses_exchange_map_and_correct_tr_id():
    session, fake = _session(use_virtual=True)
    broker = KISOverseasBroker(session, account_no="12345678", exchange_map={"AAPL": "NASDAQ", "IBM": "NYSE"})

    broker.place_order("IBM", OrderSide.BUY, 5, 150.0, datetime.now())

    order_call = next(c for c in fake.calls if "/overseas-stock/v1/trading/order" in c["url"])
    assert order_call["json"]["OVRS_EXCG_CD"] == "NYSE"
    assert order_call["json"]["OVRS_ORD_UNPR"] == "150.00"
    assert order_call["headers"]["tr_id"] == "VTTT1002U"
    assert any("/uapi/hashkey" in c["url"] for c in fake.calls)


def test_overseas_place_order_uses_real_tr_id_for_sell():
    session, fake = _session(use_virtual=False)
    broker = KISOverseasBroker(session, account_no="12345678")

    broker.place_order("AAPL", OrderSide.SELL, 5, 150.0, datetime.now())

    order_call = next(c for c in fake.calls if "/overseas-stock/v1/trading/order" in c["url"])
    assert order_call["headers"]["tr_id"] == "TTTT1006U"


def test_rate_limit_response_is_retried_with_backoff():
    session, fake = _session()
    url = f"{session.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
    fake.next_responses[url] = FakeResponse({"rt_cd": "0", "output2": [{"dnca_tot_amt": "1000000"}]})
    call_count = {"n": 0}

    original_request = fake.request

    def flaky_request(method, u, **kwargs):
        if u == url and call_count["n"] == 0:
            call_count["n"] += 1
            return FakeResponse({"rt_cd": "9", "msg_cd": "EGW00201"}, status_code=500)
        return original_request(method, u, **kwargs)

    fake.request = flaky_request
    broker = KISBroker(session, account_no="12345678")

    balance = broker.get_cash_balance()

    assert call_count["n"] == 1  # 첫 시도는 rate-limit로 실패, 재시도로 성공했어야 함
    assert balance == 1_000_000.0


def test_overseas_cash_override_used_instead_of_api_field():
    session, _ = _session()
    broker = KISOverseasBroker(session, account_no="12345678", cash_override=5000.0)

    assert broker.get_cash_balance() == 5000.0


def test_overseas_cash_override_decreases_on_buy_and_increases_on_sell():
    session, _ = _session()
    broker = KISOverseasBroker(session, account_no="12345678", exchange_map={"AAPL": "NASDAQ"}, cash_override=5000.0)

    broker.place_order("AAPL", OrderSide.BUY, 10, 100.0, datetime.now())
    assert broker.get_cash_balance() == 4000.0  # 5000 - 10*100

    broker.place_order("AAPL", OrderSide.SELL, 5, 110.0, datetime.now())
    assert broker.get_cash_balance() == 4550.0  # 4000 + 5*110


def test_overseas_cash_estimate_never_goes_negative():
    session, _ = _session()
    broker = KISOverseasBroker(session, account_no="12345678", exchange_map={"AAPL": "NASDAQ"}, cash_override=100.0)

    broker.place_order("AAPL", OrderSide.BUY, 10, 100.0, datetime.now())  # 100 - 1000 -> 음수가 되면 안 됨

    assert broker.get_cash_balance() == 0.0
