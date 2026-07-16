#!/usr/bin/env python3
"""KIS 매수/매도 주문 1건을 실제로 넣어보는 최소 테스트 (국내/해외 공용).

*** 모의투자 도메인에서만 사용할 것 (기본값이 --virtual) ***
가상 계좌라 실제 자금은 나가지 않지만, place_order()가 아직 실거래로 완전히
검증되지 않은 부분이라 이 스크립트로 직접 확인한다. 자금 부족 에러가 뜨면
그것대로 유의미하다 - 주문 형식(TR_ID/필드명/hashkey)이 KIS 서버에 정상적으로
받아들여져 '자금 심사' 단계까지 갔다는 뜻이기 때문이다.

사용법:
    python scripts/smoke_test_kis_order.py --market KRX --symbol 005930 --qty 1
    python scripts/smoke_test_kis_order.py --market US --symbol AAPL --qty 1 --exchange NASDAQ
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from trading.brokers.interfaces import OrderSide, OrderStatus
from trading.brokers.kis_broker import KISBroker
from trading.brokers.kis_overseas_broker import KISOverseasBroker
from trading.brokers.kis_session import KISSession
from trading.data.kis_overseas_provider import KISOverseasMarketDataProvider

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KRX", "US"], default="KRX")
    parser.add_argument("--symbol", default="005930", help="종목코드 (국내 예: 005930, 해외 예: AAPL)")
    parser.add_argument("--exchange", default="NASDAQ", help="--market US 일 때만 사용 (NASDAQ/NYSE/AMEX)")
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--virtual", action="store_true", default=True)
    parser.add_argument("--no-virtual", dest="virtual", action="store_false")
    parser.add_argument("--env-file", default=".env", help="자격증명을 읽을 .env 파일 (모의/실전 키를 섞이지 않게 분리 가능)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / args.env_file, override=True)
    print(f"환경변수 파일: {args.env_file}")

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    account_no = os.environ.get("KIS_ACCOUNT_NO")
    if not (app_key and app_secret and account_no):
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO를 먼저 설정하세요 (.env 확인).")

    session = KISSession(app_key, app_secret, use_virtual=args.virtual)
    account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")
    side = OrderSide.BUY if args.side == "buy" else OrderSide.SELL

    if args.market == "KRX":
        broker = KISBroker(session, account_no=account_no, account_product_code=account_product_code)
        price = 0.0  # 국내는 시장가 주문이라 가격 불필요
    else:
        exchange_map = {args.symbol: args.exchange}
        broker = KISOverseasBroker(
            session, account_no=account_no, account_product_code=account_product_code, exchange_map=exchange_map
        )
        print(f"[사전조회] {args.symbol} 현재가 조회 중 (지정가 주문에 쓸 가격)...")
        provider = KISOverseasMarketDataProvider(session, watchlist=[args.symbol], exchange_map=exchange_map)
        price_info = provider._fetch_current_price(args.symbol)  # noqa: SLF001 - 스크립트 편의상 내부 메서드 재사용
        price = price_info["price"]
        if price <= 0:
            raise SystemExit(f"{args.symbol} 현재가 조회 실패 (거래소/종목코드 확인 필요): {price_info}")
        print(f"[사전조회] 현재가 {price} 확인 - 이 가격으로 지정가 주문을 넣는다.")

    print(f"\n모드: {'모의투자' if args.virtual else '*** 실전 - 실제 자금 사용됨 ***'}")
    print(f"주문: {args.side.upper()} {args.symbol} {args.qty}주 ({'시장가' if args.market == 'KRX' else f'지정가 {price}'})")
    if args.virtual:
        confirm = input("이 주문을 모의투자 서버로 전송하시겠습니까? 'yes' 입력: ")
        if confirm != "yes":
            print("중단합니다.")
            return
    else:
        print("\n*** 실전 주문입니다. 실제 자금이 사용됩니다. ***")
        confirm = input(f"정말로 실행하려면 정확히 '{args.side.upper()} {args.symbol} {args.qty}'를 그대로 입력하세요: ")
        if confirm != f"{args.side.upper()} {args.symbol} {args.qty}":
            print("입력이 일치하지 않아 중단합니다.")
            return

    result = broker.place_order(args.symbol, side, args.qty, price=price, timestamp=datetime.now())

    print("\n" + "=" * 40)
    print(f"상태: {result.status}")
    print(f"주문번호: {result.order_id}")
    print(f"사유/메시지: {result.reason}")
    if result.status == OrderStatus.REJECTED and "부족" in (result.reason or ""):
        print("\n-> 자금 부족으로 거부됐습니다. 이건 오히려 좋은 신호입니다 - TR_ID/hashkey/필드명이")
        print("   KIS 서버에 정상적으로 받아들여져 '자금 심사' 단계까지 갔다는 뜻입니다.")


if __name__ == "__main__":
    main()
