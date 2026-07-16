import numpy as np
import pandas as pd

from trading.config import get_config
from trading.market_regime.classifier import MarketRegimeClassifier, Regime


def _index_series(drift_per_step: float, n: int = 60) -> pd.Series:
    idx = pd.date_range("2026-07-16 09:00", periods=n, freq="1min")
    values = 2600 * (1 + np.arange(n) * drift_per_step)
    return pd.Series(values, index=idx)


def test_classifies_bull_market():
    classifier = MarketRegimeClassifier(get_config())
    result = classifier.classify(_index_series(0.001))
    assert result.regime == Regime.BULL


def test_classifies_bear_market():
    classifier = MarketRegimeClassifier(get_config())
    result = classifier.classify(_index_series(-0.001))
    assert result.regime == Regime.BEAR


def test_classifies_sideways_market():
    classifier = MarketRegimeClassifier(get_config())
    result = classifier.classify(_index_series(0.0))
    assert result.regime == Regime.SIDEWAYS
