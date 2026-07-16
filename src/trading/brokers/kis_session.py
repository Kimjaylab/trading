"""한국투자증권(KIS) Open API 공용 인증 세션.

국내주식 브로커(KISBroker), 해외주식 브로커(KISOverseasBroker), 실시간 시세
공급자(KISMarketDataProvider)가 토큰 발급 로직을 각자 중복 구현하지 않도록 분리했다.
KIS는 토큰 발급을 하루 1회 정도로 제한 권장하므로, 여러 어댑터가 이 세션 객체를
공유해야 불필요한 재발급을 피할 수 있다.

*** 이 클래스는 이 개발 환경(네트워크/실계좌 접근 불가)에서 실거래 검증을 거치지 않았다. ***
"""
from __future__ import annotations

import time

import requests


class KISSession:
    REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
    VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        use_virtual: bool = True,
        http: requests.Session | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.use_virtual = use_virtual
        self.domain = self.VIRTUAL_DOMAIN if use_virtual else self.REAL_DOMAIN
        self.http = http or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        resp = self.http.post(
            f"{self.domain}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 86400))
        return self._access_token

    def headers(self, tr_id: str, extra: dict | None = None) -> dict:
        base = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            base.update(extra)
        return base
