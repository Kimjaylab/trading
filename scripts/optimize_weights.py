#!/usr/bin/env python3
"""백테스트 트레이드 로그로 스코어링 가중치를 재학습하고 config/weights.json을 갱신한다."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading.backtest.engine import BacktestEngine, BacktestResult
from trading.config import Config, get_config
from trading.data.synthetic import SyntheticDataProvider
from trading.ml.optimizer import WeightOptimizer


def _run_many_sessions(n_symbols: int, cash: float, n_sessions: int, base_seed: int, config: Config) -> BacktestResult:
    """가중치 학습에 필요한 표본을 모으기 위해 여러 (독립된) 세션의 트레이드를 합친다.

    실전에서도 ML 최적화는 하루치가 아니라 여러 거래일의 누적 트레이드 로그로 수행하므로,
    합성 데이터라도 여러 세션(=seed)을 합쳐야 현실적인 표본 크기가 된다.
    """
    combined = BacktestResult(start_equity=cash, final_equity=cash)
    for i in range(n_sessions):
        provider = SyntheticDataProvider(
            n_symbols=n_symbols, seed=base_seed + i, market_open=config.market_open, market_close=config.market_close
        )
        engine = BacktestEngine(provider, initial_cash=cash, config=config, index_close=provider._index_series)  # noqa: SLF001
        result = engine.run()
        combined.trades.extend(result.trades)
        for reason, count in result.excluded_counts.items():
            combined.excluded_counts[reason] = combined.excluded_counts.get(reason, 0) + count
    return combined


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=40)
    parser.add_argument("--sessions", type=int, default=10, help="가중치 학습용 표본을 모을 독립 세션(거래일) 수")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cash", type=float, default=50_000_000)
    parser.add_argument("--market", choices=["KRX", "US"], default="KRX", help="세션 프로필 (국내장/미국장)")
    parser.add_argument("--dry-run", action="store_true", help="가중치 파일을 저장하지 않고 결과만 출력")
    args = parser.parse_args()

    config = get_config(args.market)
    result = _run_many_sessions(args.symbols, args.cash, args.sessions, args.seed, config)

    optimizer = WeightOptimizer(config)
    if args.dry_run:
        weights, report = optimizer.fit_from_result(result)
    else:
        report = optimizer.optimize_and_save(result)

    print(f"학습 표본 수      : {report.n_samples}")
    print(f"학습 성공 여부    : {report.trained}")
    print(f"학습 정확도(train): {report.train_accuracy:.2%}")
    print(f"제거된 피처(가중치 0으로 수축): {report.dropped_features or '없음'}")
    print("전략별 학습 결과:")
    for phase, msg in report.phase_reports.items():
        print(f"  - {phase}: {msg}")

    if args.dry_run:
        print("\n(--dry-run 이므로 config/weights.json은 변경되지 않았습니다)")


if __name__ == "__main__":
    main()
