#!/usr/bin/env python3
"""KIS API 응답의 실제 JSON 구조를 그대로 출력하는 진단 도구.

get_cash_balance() 등이 예상과 다른 필드명 때문에 실패할 때, 실제 응답을 봐야
정확한 필드명/TR_ID를 알 수 있다. 이 스크립트는 파싱을 시도하지 않고 원본 그대로
출력한다. 계좌번호/예수금 등 민감할 수 있는 숫자는 공유 전에 원하면 가려도 되지만,
필드 이름(키)은 그대로 유지해야 문제를 진단할 수 있다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from trading.brokers.kis_session import KISSession

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import os

app_key = os.environ["KIS_APP_KEY"]
app_secret = os.environ["KIS_APP_SECRET"]
account_no = os.environ["KIS_ACCOUNT_NO"]
account_product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")

session = KISSession(app_key, app_secret, use_virtual=True)

params = {
    "CANO": account_no,
    "ACNT_PRDT_CD": account_product_code,
    "AFHR_FLPR_YN": "N",
    "OFL_YN": "",
    "INQR_DVSN": "02",
    "UNPR_DVSN": "01",
    "FUND_STTL_ICLD_YN": "N",
    "FNCG_AMT_AUTO_RDPT_YN": "N",
    "PRCS_DVSN": "01",
    "CTX_AREA_FK100": "",
    "CTX_AREA_NK100": "",
}

# VTTC8434R(잔고조회)만 확인하면 된다 - 예수금(output2)과 보유종목(output1)을 함께 준다.
# (VTTC8908R은 실사용자 계좌로 확인한 결과 "매수가능조회"용 TR로 밝혀져 이 목록에서 제외했다.
#  PDNO/ORD_UNPR 없이 호출하면 "INPUT_FIELD_NAME PDNO" 에러가 난다.)
for tr_id in ["VTTC8434R"]:
    print(f"\n{'=' * 20} TR_ID={tr_id} {'=' * 20}")
    resp = session.request(
        "GET",
        f"{session.domain}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=session.headers(tr_id),
        params=params,
    )
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
