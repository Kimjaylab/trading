"""실시간(페이퍼/실거래) 매매 루프.

BacktestEngine.step()과 동일한 판단 로직을 그대로 재사용한다 - 백테스트에서 검증한
필터/스코어링/전략/리스크 로직이 실거래에서도 동일하게 동작함을 보장하기 위함이다.
차이는 딱 하나: 시뮬레이션처럼 저장된 타임스탬프를 순회하는 대신, 실제 시계에 맞춰
매 분 한 번씩 step()을 호출한다.

*** 실거래 사용 전 필독 ***
이 클래스는 이 개발 환경(네트워크/실계좌 접근 불가)에서 실거래 검증이 불가능했다.
반드시 (1) 모의투자 계좌로 최소 수 주 이상 페이퍼트레이딩 -> (2) 소액 실거래 ->
(3) 정상 규모 순으로 단계적으로 검증한 뒤 사용할 것.
"""
from __future__ import annotations

import logging
import time as time_module
from datetime import datetime, time

from trading.backtest.engine import BacktestEngine, BacktestResult
from trading.brokers.interfaces import BrokerClient
from trading.config import Config, get_config
from trading.data.interfaces import MarketDataProvider
from trading.utils.time_utils import is_within_session

logger = logging.getLogger(__name__)


class LiveRunner:
    def __init__(
        self,
        data_provider: MarketDataProvider,
        broker: BrokerClient,
        initial_cash: float,
        config: Config | None = None,
        poll_interval_sec: int = 60,
    ):
        self.config = config or get_config()
        self.engine = BacktestEngine(data_provider, initial_cash, self.config, broker=broker)
        self.poll_interval_sec = poll_interval_sec
        self.result = BacktestResult(start_equity=initial_cash)
        self.excluded_counts: dict[str, int] = {}

    def _is_market_open(self, ts: datetime) -> bool:
        return is_within_session(ts, self.config.market_open, self.config.market_close)

    def run_forever(self) -> None:
        logger.info("실시간 매매 루프 시작")
        while True:
            now = datetime.now()
            if not self._is_market_open(now):
                logger.info("장 시간이 아님 (%s) - 대기", now.time())
                time_module.sleep(self.poll_interval_sec)
                continue

            try:
                self.engine.step(now, self.result, self.excluded_counts)
            except Exception:
                logger.exception("step() 처리 중 예외 발생 - 다음 주기에 재시도")

            time_module.sleep(self.poll_interval_sec)
