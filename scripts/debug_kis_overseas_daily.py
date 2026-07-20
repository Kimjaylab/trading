#!/usr/bin/env python3
"""KIS 해외주식 일봉(dailyprice) 응답을 그대로 찍고, 실제 파서(_fetch_daily_bars)로
만든 daily_bars와 그걸로 계산한 ADX 값을 함께 보여준다.

"추세 부재 (ADX 미달)"로 전종목이 막히는 게 진짜 시장 상황인지, 아니면
필드명 추측(_first_present 후보 키)이 틀려서 high/low/close가 잘못 채워지거나
날짜 순서가 뒤집혀서 생기는 버그인지 확인하기 위한 진단 스크립트.

사용법: python scripts/debug_kis_overseas_daily.py NVDA --env-file .env.real --no-virtual
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from trading.brokers.kis_exchange_codes import QUOTE_EXCHANGE_CODES
from trading.brokers.kis_session import KISSession
from trading.data.kis_overseas_provider import KISOverseasMarketDataProvider, TR_DAILY_PRICE
from trading.indicators import technical as ta

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--exchange", default="NASDAQ")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--virtual", action="store_true", default=True)
    parser.add_argument("--no-virtual", dest="virtual", action="store_false")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / args.env_file, override=True)
    session = KISSession(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"], use_virtual=args.virtual)

    print(f"{'=' * 20} 원본 dailyprice 응답 (output2 앞 5행) {'=' * 20}")
    resp = session.request(
        "GET",
        f"{session.domain}/uapi/overseas-price/v1/quotations/dailyprice",
        headers=session.headers(TR_DAILY_PRICE),
        params={"AUTH": "", "EXCD": QUOTE_EXCHANGE_CODES[args.exchange], "SYMB": args.symbol, "GUBN": "0", "BYMD": "", "MODP": "0"},
    )
    body = resp.json()
    rows = body.get("output2", [])
    print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
    first_row_dates = {k: rows[0].get(k) for k in ("xymd", "stck_bsop_date", "zdiv")} if rows else {}
    print(f"\n총 {len(rows)}행. 첫 행(응답상 맨 위) 날짜 후보 키들: {first_row_dates}")
    last_row_dates = {k: rows[-1].get(k) for k in ("xymd", "stck_bsop_date", "zdiv")} if rows else {}
    print(f"마지막 행(응답상 맨 아래) 날짜 후보 키들: {last_row_dates}")

    print(f"\n{'=' * 20} 파서(_fetch_daily_bars)로 만든 daily_bars {'=' * 20}")
    provider = KISOverseasMarketDataProvider(session, watchlist=[args.symbol], exchange_map={args.symbol: args.exchange})
    daily = provider._fetch_daily_bars(args.symbol)
    print(daily.tail(20).to_string())

    if len(daily) >= 15:
        adx_series = ta.adx(daily["high"], daily["low"], daily["close"], window=14)
        print(f"\n계산된 ADX (최근 5개): {adx_series.tail(5).tolist()}")
        print(f"현재(마지막) ADX: {adx_series.iloc[-1]:.2f}")
    else:
        print(f"\n일봉이 {len(daily)}개뿐이라 ADX 계산(15개 이상 필요) 불가")


if __name__ == "__main__":
    main()
