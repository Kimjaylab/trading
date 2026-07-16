#!/usr/bin/env python3
"""실시간(페이퍼/KIS 모의투자/실전) 매매 루프를 실행한다.

*** 이 스크립트는 이 개발 환경에서 실행 검증(네트워크/실계좌)을 하지 못했다. ***
반드시 --broker paper로 먼저 로직을 확인하고, 그 다음 --broker kis --virtual로
KIS 모의투자 계좌를 연결해 충분히 검증한 뒤에만 --no-virtual(실전)을 사용할 것.

필요한 환경변수 (KIS 브로커 사용 시):
  KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, (선택) KIS_ACCOUNT_PRODUCT_CODE
repo 루트에 .env 파일(.env.example 참고)을 만들어두면 자동으로 읽는다 - 매번 export할 필요 없음.

--market US로 --broker kis를 쓰면 KISOverseasBroker + KISOverseasMarketDataProvider가
연결된다. 이 해외 경로는 국내(KRX) 경로보다 신뢰도가 더 낮다 - 특히 분봉 데이터가
없어 폴링할 때마다 받은 현재가를 1개 봉으로 근사한다(brokers/kis_overseas_broker.py,
data/kis_overseas_provider.py 상단 주석 참고). --exchange-map으로 종목별 거래소를
지정할 수 있다 (예: AAPL:NASDAQ,IBM:NYSE). 지정하지 않으면 전부 NASDAQ으로 가정한다.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from trading.brokers.kis_broker import KISBroker
from trading.brokers.kis_overseas_broker import KISOverseasBroker
from trading.brokers.kis_session import KISSession
from trading.brokers.paper_broker import PaperBroker
from trading.config import get_config
from trading.data.kis_overseas_provider import KISOverseasMarketDataProvider
from trading.data.kis_provider import KISMarketDataProvider
from trading.data.synthetic import SyntheticDataProvider
from trading.execution.live_runner import LiveRunner

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KRX", "US"], default="KRX")
    parser.add_argument("--broker", choices=["paper", "kis"], default="paper")
    parser.add_argument("--cash", type=float, default=50_000_000)
    parser.add_argument("--virtual", action="store_true", default=True, help="KIS 모의투자 도메인 사용 (기본값)")
    parser.add_argument("--no-virtual", dest="virtual", action="store_false", help="KIS 실전 도메인 사용 - 매우 신중히 사용할 것")
    parser.add_argument(
        "--watchlist", type=str, default="", help="쉼표로 구분한 관심종목 코드 (--broker kis일 때 필수, 예: 005930,000660 또는 AAPL,MSFT)"
    )
    parser.add_argument(
        "--exchange-map", type=str, default="",
        help="--market US --broker kis 일 때만 사용. 종목별 거래소 (예: AAPL:NASDAQ,IBM:NYSE). 미지정 종목은 NASDAQ.",
    )
    parser.add_argument("--poll-interval", type=int, default=60)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = get_config(args.market)

    if args.broker == "paper":
        provider = SyntheticDataProvider(n_symbols=24, market_open=config.market_open, market_close=config.market_close)
        broker = PaperBroker(provider, args.cash)
    else:
        if not args.watchlist:
            raise SystemExit("--broker kis 사용 시 --watchlist로 관심종목을 지정해야 한다.")

        app_key = os.environ.get("KIS_APP_KEY")
        app_secret = os.environ.get("KIS_APP_SECRET")
        account_no = os.environ.get("KIS_ACCOUNT_NO")
        if not (app_key and app_secret and account_no):
            raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO를 설정해야 한다.")

        session = KISSession(app_key, app_secret, use_virtual=args.virtual)
        account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")
        watchlist = args.watchlist.split(",")

        if args.market == "KRX":
            broker = KISBroker(session, account_no=account_no, account_product_code=account_product_code)
            provider = KISMarketDataProvider(session, watchlist=watchlist)
        else:
            exchange_map = {}
            for pair in args.exchange_map.split(","):
                if ":" in pair:
                    sym, exch = pair.split(":", 1)
                    exchange_map[sym.strip()] = exch.strip()
            broker = KISOverseasBroker(
                session, account_no=account_no, account_product_code=account_product_code, exchange_map=exchange_map
            )
            provider = KISOverseasMarketDataProvider(session, watchlist=watchlist, exchange_map=exchange_map)
            logging.warning(
                "미국장 KIS 연동은 분봉 데이터가 없어 폴링 간격을 1개 봉으로 근사합니다 - "
                "신뢰도가 국내(KRX) 경로보다 낮습니다."
            )

        if not args.virtual:
            logging.warning("!!! 실전 도메인(--no-virtual)으로 실행합니다. 실제 자금이 사용됩니다 !!!")

    runner = LiveRunner(provider, broker, initial_cash=args.cash, config=config, poll_interval_sec=args.poll_interval)
    runner.run_forever()


if __name__ == "__main__":
    main()
