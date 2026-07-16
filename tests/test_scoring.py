from datetime import time

from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider
from trading.scoring.engine import ScoringEngine
from trading.scoring.features import FEATURE_NAMES, FeatureExtractor


def _provider():
    return SyntheticDataProvider(n_symbols=6, seed=1, bar_minutes=15)


def test_feature_extraction_within_bounds():
    provider = _provider()
    extractor = FeatureExtractor(get_config())
    ts = provider.get_session_timestamps()[5]
    snapshots = {sym: provider.get_snapshot(sym, ts) for sym in provider.get_universe(ts)}
    features = extractor.extract_batch(snapshots)

    assert len(features) == 6
    for fv in features.values():
        for name in FEATURE_NAMES:
            value = getattr(fv, name)
            assert -1.0 - 1e-6 <= value <= 1.0 + 1e-6, f"{name}={value} out of bounds"


def test_scoring_engine_returns_probability():
    provider = _provider()
    extractor = FeatureExtractor(get_config())
    engine = ScoringEngine(get_config())
    ts = provider.get_session_timestamps()[5]
    symbol = provider.get_universe(ts)[0]
    snapshot = provider.get_snapshot(symbol, ts)
    features = extractor.extract_single(snapshot)

    for phase in ["breakout", "pullback", "trend_pullback"]:
        result = engine.score(features, phase)
        assert 0.0 <= result.score <= 1.0
        assert set(result.contributions.keys()) == set(FEATURE_NAMES)


def test_phase_weight_override_changes_score():
    provider = _provider()
    extractor = FeatureExtractor(get_config())
    engine = ScoringEngine(get_config())
    ts = provider.get_session_timestamps()[5]
    symbol = provider.get_universe(ts)[0]
    snapshot = provider.get_snapshot(symbol, ts)
    features = extractor.extract_single(snapshot)

    bias_b, weights_b = engine.weight_vector("breakout")
    bias_t, weights_t = engine.weight_vector("trend_pullback")
    assert weights_b != weights_t
