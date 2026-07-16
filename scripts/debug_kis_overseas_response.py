#!/usr/bin/env python3
"""KIS 해외주식 잔고조회 응답의 실제 JSON 구조를 그대로 출력하는 진단 도구.

get_cash_balance()가 쓰는 "inquire-psamount" 엔드포인트가 국내 때(VTTC8908R)와
비슷하게 잘못된 TR/엔드포인트일 가능성이 있다. get_positions()가 쓰는
"inquire-balance"(TR VTTS3012R) 응답에 예수금 정보가 이미 포함되어 있는지
확인하기 위해 원본 그대로 출력한다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from trading.brokers.kis_exchange_codes import TRADING_EXCHANGE_CODES
from trading.brokers.kis_session import KISSession

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app_key = os.environ["KIS_APP_KEY"]
app_secret = os.environ["KIS_APP_SECRET"]
account_no = os.environ["KIS_ACCOUNT_NO"]
account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")

session = KISSession(app_key, app_secret, use_virtual=True)

params = {
    "CANO": account_no,
    "ACNT_PRDT_CD": account_product_code,
    "OVRS_EXCG_CD": TRADING_EXCHANGE_CODES["NASDAQ"],
    "TR_CRCY_CD": "USD",
    "CTX_AREA_FK200": "",
    "CTX_AREA_NK200": "",
}

for label, path in [
    ("inquire-balance (get_positions가 사용)", "inquire-balance"),
    ("inquire-psamount (get_cash_balance가 사용, 문제 의심)", "inquire-psamount"),
]:
    print(f"\n{'=' * 20} {label} {'=' * 20}")
    resp = session.request(
        "GET",
        f"{session.domain}/uapi/overseas-stock/v1/trading/{path}",
        headers=session.headers("VTTS3012R"),
        params=params,
    )
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
