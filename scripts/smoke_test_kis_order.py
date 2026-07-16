#!/usr/bin/env python3
"""KIS 국내주식 매수 주문 1건을 실제로 넣어보는 최소 테스트.

*** 모의투자 도메인에서만 사용할 것 (기본값이 --virtual) ***
가상 계좌라 실제 자금은 나가지 않지만, place_order()가 아직 실거래로 검증되지
않은 마지막 조각이라 이 스크립트로 직접 확인한다. 예수금이 부족해도 상관없다 -
"자금 부족" 에러가 뜨면 그것대로 주문 형식(TR_ID/필드명/hashkey)이 KIS 서버에
정상적으로 받아들여졌다는 뜻이므로 유의미한 결과다.

사용법:
    python scripts/smoke_test_kis_order.py --symbol 005930 --qty 1
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
from trading.brokers.kis_session import KISSession

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="005930", help="종목코드 (기본: 005930 삼성전자)")
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--virtual", action="store_true", default=True)
    parser.add_argument("--no-virtual", dest="virtual", action="store_false")
    args = parser.parse_args()

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    account_no = os.environ.get("KIS_ACCOUNT_NO")
    if not (app_key and app_secret and account_no):
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO를 먼저 설정하세요 (.env 확인).")

    print(f"모드: {'모의투자' if args.virtual else '*** 실전 - 실제 자금 사용됨 ***'}")
    print(f"주문: {args.side.upper()} {args.symbol} {args.qty}주 (시장가)")
    confirm = input("이 주문을 실제로 전송하시겠습니까? 'yes' 입력: ")
    if confirm != "yes":
        print("중단합니다.")
        return

    session = KISSession(app_key, app_secret, use_virtual=args.virtual)
    account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")
    broker = KISBroker(session, account_no=account_no, account_product_code=account_product_code)

    side = OrderSide.BUY if args.side == "buy" else OrderSide.SELL
    result = broker.place_order(args.symbol, side, args.qty, price=0.0, timestamp=datetime.now())

    print("\n" + "=" * 40)
    print(f"상태: {result.status}")
    print(f"주문번호: {result.order_id}")
    print(f"사유/메시지: {result.reason}")
    if result.status == OrderStatus.REJECTED and "부족" in (result.reason or ""):
        print("\n-> 자금 부족으로 거부됐습니다. 이건 오히려 좋은 신호입니다 - TR_ID/hashkey/필드명이")
        print("   KIS 서버에 정상적으로 받아들여져 '자금 심사' 단계까지 갔다는 뜻입니다.")


if __name__ == "__main__":
    main()
