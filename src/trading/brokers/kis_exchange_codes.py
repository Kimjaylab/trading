"""KIS 해외주식 거래소 코드 - 주문/잔고(trading) API와 시세(quotations) API가
서로 다른 코드 체계를 쓴다고 알려져 있다 (trading=4자리, quotations=3자리).

*** 확인 필요 ***: 이 두 체계가 실제로 다르다는 것은 공개 문서/커뮤니티에서 흔히
언급되지만, 이 환경에서 실제 호출로 검증하지 못했다. 종목을 지정할 때는 아래
캐노니컬 이름("NASDAQ"/"NYSE"/"AMEX")을 쓰고, 각 API 호출부가 알맞은 코드로
변환하도록 설계했다 - 두 체계를 헷갈려 직접 문자열을 넣는 실수를 줄이기 위함이다.
"""
from __future__ import annotations

# 주문/잔고 등 거래(trading) API의 OVRS_EXCG_CD
TRADING_EXCHANGE_CODES: dict[str, str] = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
}

# 현재가/일봉 등 시세(quotations) API의 EXCD
QUOTE_EXCHANGE_CODES: dict[str, str] = {
    "NASDAQ": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}

DEFAULT_EXCHANGE = "NASDAQ"
