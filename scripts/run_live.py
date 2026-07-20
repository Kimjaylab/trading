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

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    parser.add_argument("--env-file", default=".env", help="자격증명을 읽을 .env 파일 (모의/실전 키를 섞이지 않게 분리 가능)")
    parser.add_argument(
        "--cash-override", type=float, default=None,
        help="--market US --broker kis 전용. 해외 예수금 API 필드를 찾지 못해 0으로 나올 때, "
             "KIS 앱에서 확인한 실제 USD 예수금을 직접 지정한다 (매수/매도마다 로컬에서 증감 추적).",
    )
    parser.add_argument(
        "--usd-krw-rate", type=float, default=1450.0,
        help="--market US --broker kis 전용. 거래대금(원화 기준 필터)을 USD에서 환산할 때 쓸 원/달러 환율 근사치. "
             "KIS 앱/증권사 고시환율을 참고해 조정하면 유동성 필터가 더 정확해진다 (기본값 1450).",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"],
        help="DEBUG로 하면 각 종목이 왜 제외/보류됐는지(점수, 사유)까지 매 주기마다 출력한다.",
    )
    parser.add_argument(
        "--ignore-symbols", type=str, default="",
        help="쉼표로 구분한, 이 프로그램이 절대 매수/매도하지 않을 종목 (예: 본인이 수동으로 산 종목). "
             "계좌에 이미 보유 중이어도 완전히 무시한다.",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / args.env_file, override=True)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    logging.info("환경변수 파일: %s / 로그레벨: %s", args.env_file, args.log_level)

    config = get_config(args.market)
    ignore_symbols = {s.strip() for s in args.ignore_symbols.split(",") if s.strip()}
    if ignore_symbols:
        logging.info("관리 제외 종목(건드리지 않음): %s", ignore_symbols)

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
                session, account_no=account_no, account_product_code=account_product_code, exchange_map=exchange_map,
                cash_override=args.cash_override,
            )
            if args.cash_override is not None:
                logging.info("해외 예수금을 수동 지정값(%.2f USD)으로 사용합니다.", args.cash_override)
            provider = KISOverseasMarketDataProvider(
                session, watchlist=watchlist, exchange_map=exchange_map, usd_krw_rate=args.usd_krw_rate,
            )
            logging.info("해외 거래대금 환산 환율: %.2f KRW/USD", args.usd_krw_rate)
            logging.warning(
                "미국장 KIS 연동은 분봉 데이터가 없어 폴링 간격을 1개 봉으로 근사합니다 - "
                "신뢰도가 국내(KRX) 경로보다 낮습니다."
            )

        if not args.virtual:
            logging.warning("!!! 실전 도메인(--no-virtual)으로 실행합니다. 실제 자금이 사용됩니다 !!!")
            confirm = input(f"실전 자동매매를 시작하려면 정확히 '실전 {args.market} 시작'을 입력하세요: ")
            if confirm != f"실전 {args.market} 시작":
                raise SystemExit("입력이 일치하지 않아 중단합니다.")

        # KIS 실계좌/모의계좌는 실제 예수금으로 RiskManager 기준 자산을 잡아야 한다.
        # --cash는 paper 브로커 전용 값이라 여기서는 쓰지 않는다.
        real_cash = broker.get_cash_balance()
        logging.info("계좌 조회 결과 예수금: %.0f", real_cash)
        args.cash = real_cash

    runner = LiveRunner(
        provider, broker, initial_cash=args.cash, config=config, poll_interval_sec=args.poll_interval,
        ignore_symbols=ignore_symbols,
    )
    runner.run_forever()


if __name__ == "__main__":
    main()
