"""백테스트 결과에서 성과 지표를 계산한다."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trading.backtest.engine import BacktestResult, TradeRecord


@dataclass
class PerformanceMetrics:
    n_trades: int
    win_rate: float
    avg_return_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    payoff_ratio: float  # avg_win / |avg_loss|
    profit_factor: float  # 총이익 / 총손실(절대값)
    max_drawdown_pct: float
    total_return_pct: float
    expectancy_pct: float  # 승률*평균수익 + 패률*평균손실, 1건당 기대수익률


def compute_metrics(result: BacktestResult) -> PerformanceMetrics:
    trades = result.trades
    n = len(trades)
    if n == 0:
        return PerformanceMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    returns = np.array([t.pnl_pct for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    win_rate = len(wins) / n
    avg_return = float(returns.mean())
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")

    gross_profit = float(sum(t.pnl_krw for t in trades if t.pnl_krw > 0))
    gross_loss = float(abs(sum(t.pnl_krw for t in trades if t.pnl_krw <= 0)))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity = np.array([e for _, e in result.equity_curve]) if result.equity_curve else np.array([result.start_equity])
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max * 100
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    total_return = (result.final_equity / result.start_equity - 1) * 100 if result.start_equity else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return PerformanceMetrics(
        n_trades=n,
        win_rate=win_rate,
        avg_return_pct=avg_return,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        total_return_pct=total_return,
        expectancy_pct=expectancy,
    )


def metrics_by_phase(result: BacktestResult) -> dict[str, PerformanceMetrics]:
    phases = sorted({t.phase for t in result.trades})
    out = {}
    for phase in phases:
        subset = BacktestResult(
            trades=[t for t in result.trades if t.phase == phase],
            equity_curve=result.equity_curve,
            final_equity=result.final_equity,
            start_equity=result.start_equity,
        )
        out[phase] = compute_metrics(subset)
    return out
