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
