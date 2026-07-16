"""KISOverseasMarketDataProvider의 스냅샷 구성 로직을 네트워크 없이 검증한다."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

from trading.brokers.kis_session import KISSession
from trading.data.kis_overseas_provider import KISOverseasMarketDataProvider


class FakeResponse:
    def __init__(self, json_body: dict):
        self._json = json_body
        self.status_code = 200
        self.text = str(json_body)

    def json(self):
        return self._json


class FakeHTTP:
    def __init__(self):
        self.price_sequence: list[float] = [100.0]
        self.volume_sequence: list[float] = [1000.0]
        self._call_index = 0

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, **kwargs):
        if "/oauth2/tokenP" in url:
            return FakeResponse({"access_token": "fake-token", "expires_in": 86400})
        if "/quotations/price-detail" in url:
            idx = min(self._call_index, len(self.price_sequence) - 1)
            price = self.price_sequence[idx]
            volume = self.volume_sequence[idx]
            self._call_index += 1
            return FakeResponse({"output": {"last": str(price), "tvol": str(volume)}})
        if "/quotations/dailyprice" in url:
            # 도메인 코드 가정과 동일하게 최신순(내림차순)으로 응답한다고 가정 - _fetch_daily_bars가 뒤집어 시간순으로 만든다.
            rows = [
                {"xymd": "20260711", "open": "99", "high": "103", "low": "98", "clos": "102", "tvol": "600000"},
                {"xymd": "20260710", "open": "95", "high": "101", "low": "94", "clos": "99", "tvol": "500000"},
            ]
            return FakeResponse({"output2": rows})
        raise AssertionError(f"unexpected URL: {url}")


def _provider(price_sequence=None) -> KISOverseasMarketDataProvider:
    fake = FakeHTTP()
    if price_sequence:
        fake.price_sequence = price_sequence
        fake.volume_sequence = [1000.0 * (i + 1) for i in range(len(price_sequence))]
    session = KISSession(
        "appkey", "appsecret", use_virtual=True, http=fake,
        request_interval_sec=0.0, token_cache_path=tempfile.mktemp(suffix=".json"),
    )
    return KISOverseasMarketDataProvider(session, watchlist=["AAPL"], exchange_map={"AAPL": "NASDAQ"})


def test_get_universe_returns_watchlist():
    provider = _provider()
    assert provider.get_universe(datetime.now()) == ["AAPL"]


def test_snapshot_builds_daily_bars_from_dailyprice_endpoint():
    provider = _provider()
    snapshot = provider.get_snapshot("AAPL", datetime.now())

    assert list(snapshot.daily_bars["close"]) == [99.0, 102.0]
    assert snapshot.daily_bars["trading_value"].iloc[-1] == 102.0 * 600000


def test_minute_buffer_accumulates_across_polls():
    provider = _provider(price_sequence=[100.0, 101.0, 99.5])
    t0 = datetime.now()

    snap1 = provider.get_snapshot("AAPL", t0)
    snap2 = provider.get_snapshot("AAPL", t0 + timedelta(minutes=1))
    snap3 = provider.get_snapshot("AAPL", t0 + timedelta(minutes=2))

    assert len(snap1.minute_bars) == 1
    assert len(snap2.minute_bars) == 2
    assert len(snap3.minute_bars) == 3
    assert list(snap3.minute_bars["close"]) == [100.0, 101.0, 99.5]


def test_neutral_defaults_for_unavailable_features():
    provider = _provider()
    snapshot = provider.get_snapshot("AAPL", datetime.now())

    assert snapshot.vi_triggered is False
    assert snapshot.program_net_buy_krw == 0.0
    assert snapshot.execution_strength == 100.0
