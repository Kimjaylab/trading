#!/usr/bin/env python3
"""합성 데이터로 백테스트를 실행하고 성과/조건별 리포트를 출력한다.

실데이터로 돌리려면 SyntheticDataProvider 대신 CSVDataProvider(data_dir=...)를 사용하면 된다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from trading.backtest.engine import BacktestEngine
from trading.backtest.metrics import compute_metrics, metrics_by_phase
from trading.backtest.report import exclusion_report, exit_reason_report, feature_condition_report
from trading.config import get_config
from trading.data.synthetic import SyntheticDataProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cash", type=float, default=50_000_000)
    parser.add_argument("--market", choices=["KRX", "US"], default="KRX", help="세션 프로필 (국내장/미국장)")
    parser.add_argument("--save-trades", type=str, default=None, help="트레이드 로그를 CSV로 저장할 경로")
    args = parser.parse_args()

    config = get_config(args.market)
    provider = SyntheticDataProvider(
        n_symbols=args.symbols, seed=args.seed, market_open=config.market_open, market_close=config.market_close
    )
    index_close = provider._index_series  # noqa: SLF001 - 스크립트 편의상 내부 시리즈 재사용

    engine = BacktestEngine(provider, initial_cash=args.cash, config=config, index_close=index_close)
    result = engine.run()

    metrics = compute_metrics(result)
    print("=== 전체 성과 ===")
    print(f"거래 건수        : {metrics.n_trades}")
    print(f"승률             : {metrics.win_rate:.1%}")
    print(f"평균 수익률      : {metrics.avg_return_pct:.2f}%")
    print(f"평균 익절/손절   : {metrics.avg_win_pct:.2f}% / {metrics.avg_loss_pct:.2f}%")
    print(f"손익비(payoff)   : {metrics.payoff_ratio:.2f}")
    print(f"Profit Factor    : {metrics.profit_factor:.2f}")
    print(f"MDD              : {metrics.max_drawdown_pct:.2f}%")
    print(f"총 수익률        : {metrics.total_return_pct:.2f}%")
    print(f"기대수익(1건당)  : {metrics.expectancy_pct:.2f}%")

    print("\n=== 전략(시간대)별 성과 ===")
    for phase, m in metrics_by_phase(result).items():
        print(f"[{phase:14s}] n={m.n_trades:3d}  승률={m.win_rate:.1%}  평균수익={m.avg_return_pct:+.2f}%  PF={m.profit_factor:.2f}")

    print("\n=== 청산 사유별 통계 ===")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        print(exit_reason_report(result).to_string(index=False))

    print("\n=== 제외필터 발동 통계 ===")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        print(exclusion_report(result).to_string(index=False))

    print("\n=== 조건(피처)별 승률/기대수익 (상세는 --save-trades로 저장 후 분석 권장) ===")
    cond = feature_condition_report(result)
    if not cond.empty:
        with pd.option_context("display.max_rows", None, "display.width", 120):
            print(cond.to_string(index=False))
    else:
        print("(거래 건수가 부족해 조건별 분석을 생성할 수 없습니다)")

    if args.save_trades:
        from trading.backtest.report import trades_to_dataframe

        trades_to_dataframe(result).to_csv(args.save_trades, index=False)
        print(f"\n트레이드 로그 저장: {args.save_trades}")


if __name__ == "__main__":
    main()
