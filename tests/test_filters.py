import dataclasses

from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider
from trading.filters.exclusion import is_excluded


def test_thin_liquidity_symbol_is_excluded():
    provider = SyntheticDataProvider(n_symbols=6, seed=2, bar_minutes=5)
    ts = provider.get_session_timestamps()[10]
    thin_symbol = next(p.symbol for p in provider._symbols if p.scenario == "thin_liquidity")
    snapshot = provider.get_snapshot(thin_symbol, ts)

    excluded, reasons = is_excluded(snapshot, "breakout", get_config())
    assert excluded
    assert len(reasons) > 0


def test_healthy_symbol_not_necessarily_excluded():
    provider = SyntheticDataProvider(n_symbols=6, seed=2, bar_minutes=5)
    ts = provider.get_session_timestamps()[10]
    breakout_symbol = next(p.symbol for p in provider._symbols if p.scenario == "breakout")
    snapshot = provider.get_snapshot(breakout_symbol, ts)

    excluded, reasons = is_excluded(snapshot, "breakout", get_config())
    # 반드시 통과해야 하는 건 아니지만(과열 필터에 걸릴 수도 있음), 예외 없이 판정되어야 한다.
    assert isinstance(excluded, bool)
    assert isinstance(reasons, list)


def test_zero_orderbook_depth_is_treated_as_missing_data_not_thin_book():
    """해외주식은 호가 조회를 구현하지 않아 bid/ask_qty_top5가 항상 0이다. 이를 '얇은 호가'로
    오판해 모든 종목을 걸러버리면 안 되고, '데이터 없음'으로 보고 이 필터만 건너뛰어야 한다."""
    provider = SyntheticDataProvider(n_symbols=6, seed=2, bar_minutes=5)
    ts = provider.get_session_timestamps()[10]
    breakout_symbol = next(p.symbol for p in provider._symbols if p.scenario == "breakout")
    snapshot = provider.get_snapshot(breakout_symbol, ts)
    zero_depth_snapshot = dataclasses.replace(snapshot, bid_qty_top5=0, ask_qty_top5=0)

    _, reasons = is_excluded(zero_depth_snapshot, "breakout", get_config())
    assert not any("호가 잔량 과소" in r for r in reasons)


def test_genuinely_thin_orderbook_still_excluded_when_data_present():
    """호가 데이터가 실제로 존재하는데(0이 아닌데) 임계치보다 얇으면 여전히 제외되어야 한다 -
    위 fix가 필터 자체를 무력화시킨 게 아니라 '데이터 없음' 케이스만 골라 건너뛰는지 확인."""
    provider = SyntheticDataProvider(n_symbols=6, seed=2, bar_minutes=5)
    ts = provider.get_session_timestamps()[10]
    breakout_symbol = next(p.symbol for p in provider._symbols if p.scenario == "breakout")
    snapshot = provider.get_snapshot(breakout_symbol, ts)
    thin_snapshot = dataclasses.replace(snapshot, bid_qty_top5=1, ask_qty_top5=1)

    _, reasons = is_excluded(thin_snapshot, "breakout", get_config())
    assert any("호가 잔량 과소" in r for r in reasons)
