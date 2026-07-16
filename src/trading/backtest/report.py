"""조건(피처)별 승률/기대수익 분석 리포트.

'백테스트를 통해 각 조건의 효과를 검증한다'를 실제로 수행하는 모듈이다.
각 피처를 3분위(하위/중위/상위)로 나눠 구간별 승률과 평균수익을 비교하면,
어떤 조건이 실제로 수익에 기여하는지, 어떤 조건이 노이즈에 가까운지 드러난다.
"""
from __future__ import annotations

import pandas as pd

from trading.backtest.engine import BacktestResult
from trading.scoring.features import FEATURE_NAMES


def trades_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        row = {
            "symbol": t.symbol,
            "phase": t.phase,
            "entry_ts": t.entry_ts,
            "exit_ts": t.exit_ts,
            "pnl_pct": t.pnl_pct,
            "pnl_krw": t.pnl_krw,
            "win": t.win,
            "exit_reason": t.exit_reason,
            "entry_score": t.entry_score,
        }
        row.update(t.features_at_entry)
        rows.append(row)
    return pd.DataFrame(rows)


def feature_condition_report(result: BacktestResult, n_buckets: int = 3) -> pd.DataFrame:
    """피처별 구간(저/중/고) 승률·평균수익·표본수를 담은 데이터프레임."""
    df = trades_to_dataframe(result)
    if df.empty:
        return pd.DataFrame(columns=["feature", "bucket", "n", "win_rate", "avg_return_pct"])

    labels = ["low", "mid", "high"][:n_buckets] if n_buckets == 3 else [f"q{i+1}" for i in range(n_buckets)]
    rows = []
    for feature in FEATURE_NAMES:
        if feature not in df.columns:
            continue
        try:
            bucketed = pd.qcut(df[feature], q=n_buckets, labels=labels, duplicates="drop")
        except ValueError:
            continue
        for bucket in bucketed.dropna().unique():
            subset = df[bucketed == bucket]
            rows.append(
                {
                    "feature": feature,
                    "bucket": bucket,
                    "n": len(subset),
                    "win_rate": subset["win"].mean(),
                    "avg_return_pct": subset["pnl_pct"].mean(),
                }
            )
    return pd.DataFrame(rows)


def exit_reason_report(result: BacktestResult) -> pd.DataFrame:
    df = trades_to_dataframe(result)
    if df.empty:
        return pd.DataFrame(columns=["exit_reason", "n", "win_rate", "avg_return_pct"])
    grouped = df.groupby("exit_reason").agg(n=("win", "size"), win_rate=("win", "mean"), avg_return_pct=("pnl_pct", "mean"))
    return grouped.reset_index().sort_values("n", ascending=False)


def exclusion_report(result: BacktestResult) -> pd.DataFrame:
    return pd.DataFrame(
        [{"reason": k, "count": v} for k, v in sorted(result.excluded_counts.items(), key=lambda kv: -kv[1])]
    )
