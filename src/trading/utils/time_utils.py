"""장 시작 시각 기준 경과 시간 계산 유틸리티.

자정을 넘기는 세션(미국장: 22:30~05:00 KST)을 지원하기 위해, 단순 시각 비교
(ts.time() >= X) 대신 이 모듈의 함수들을 통해서만 세션 경계를 판정해야 한다.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta


def minutes_since_open(ts: datetime, market_open: time) -> float:
    """ts로부터 가장 최근에 지난 market_open 시각까지 경과한 분.

    자정을 넘기는 세션(예: 22:30 개장, 다음날 03:00 조회)에서도 "오늘 22:30"이
    아직 오지 않았다면 "어제 22:30"을 기준으로 삼아 경과 시간을 올바르게 계산한다.
    """
    open_dt = ts.replace(hour=market_open.hour, minute=market_open.minute, second=0, microsecond=0)
    if ts < open_dt:
        open_dt -= timedelta(days=1)
    return (ts - open_dt).total_seconds() / 60.0


def minutes_until(ts: datetime, target: time) -> float:
    """ts로부터 target 시각까지 남은 분. target이 이미 지났다면 다음날 target까지의 분."""
    target_dt = ts.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if target_dt < ts:
        target_dt += timedelta(days=1)
    return (target_dt - ts).total_seconds() / 60.0


def minutes_between(start: time, end: time) -> float:
    """start부터 end까지의 분(항상 양수). end가 start보다 이르면 자정을 넘긴 것으로 간주."""
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    diff = end_min - start_min
    if diff < 0:
        diff += 24 * 60
    return float(diff)


def is_within_session(ts: datetime, market_open: time, market_close: time) -> bool:
    """자정을 넘기는 세션도 올바르게 판정하는 '장중 여부' 체크."""
    session_length = minutes_between(market_open, market_close)
    elapsed = minutes_since_open(ts, market_open)
    return 0 <= elapsed <= session_length
