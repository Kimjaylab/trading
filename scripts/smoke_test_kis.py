#!/usr/bin/env python3
"""KIS 연동을 실제 주문 없이 안전하게 점검하는 스모크테스트.

주문(place_order)은 절대 호출하지 않는다 - 토큰 발급, hashkey 발급, 잔고 조회처럼
계좌에 영향을 주지 않는 읽기 전용 호출만 수행한다. 실거래 루프(run_live.py)를
돌리기 전에 자격증명/TR_ID/네트워크가 제대로 동작하는지 먼저 이걸로 확인할 것.

사용법:
    export KIS_APP_KEY=...
    export KIS_APP_SECRET=...
    export KIS_ACCOUNT_NO=...
    python scripts/smoke_test_kis.py --market KRX          # 국내, 모의투자(기본값)
    python scripts/smoke_test_kis.py --market US            # 해외, 모의투자
    python scripts/smoke_test_kis.py --market KRX --no-virtual   # 실전 - 신중히
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading.brokers.kis_broker import KISBroker
from trading.brokers.kis_overseas_broker import KISOverseasBroker
from trading.brokers.kis_session import KISSession


def _step(name: str, fn) -> bool:
    print(f"\n[{name}] 시도 중...")
    try:
        result = fn()
        print(f"[{name}] 성공: {result}")
        return True
    except Exception:
        print(f"[{name}] 실패:")
        traceback.print_exc()
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KRX", "US"], default="KRX")
    parser.add_argument("--virtual", action="store_true", default=True)
    parser.add_argument("--no-virtual", dest="virtual", action="store_false")
    args = parser.parse_args()

    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    account_no = os.environ.get("KIS_ACCOUNT_NO")
    if not (app_key and app_secret and account_no):
        raise SystemExit("환경변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO를 먼저 설정하세요.")

    print(f"모드: {'모의투자' if args.virtual else '*** 실전 ***'} / 시장: {args.market}")
    if not args.virtual:
        confirm = input("실전 도메인입니다. 계속하려면 'yes'를 입력하세요: ")
        if confirm != "yes":
            print("중단합니다.")
            return

    session = KISSession(app_key, app_secret, use_virtual=args.virtual)
    account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")

    ok = True
    ok &= _step("1. 토큰 발급", session.ensure_token)
    ok &= _step("2. hashkey 발급 (더미 주문본문)", lambda: session.get_hashkey({"test": "1"}))

    if args.market == "KRX":
        broker = KISBroker(session, account_no=account_no, account_product_code=account_product_code)
        ok &= _step("3. 국내 예수금 조회", broker.get_cash_balance)
        ok &= _step("4. 국내 잔고(보유종목) 조회", broker.get_positions)
    else:
        broker = KISOverseasBroker(session, account_no=account_no, account_product_code=account_product_code)
        ok &= _step("3. 해외 예수금 조회", broker.get_cash_balance)
        ok &= _step("4. 해외 잔고(보유종목) 조회", broker.get_positions)

    print("\n" + ("=" * 40))
    print("모두 성공했습니다. TR_ID/응답 파싱이 최소한 이 계좌 환경에서는 맞다는 뜻입니다." if ok
          else "일부 실패했습니다. 위 트레이스백의 TR_ID/엔드포인트/필드명을 KIS 공식 문서와 대조하세요.")


if __name__ == "__main__":
    main()
