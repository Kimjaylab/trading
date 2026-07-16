"""한국투자증권(KIS) Open API 공용 인증 세션.

국내주식 브로커(KISBroker), 해외주식 브로커(KISOverseasBroker), 실시간 시세
공급자(KISMarketDataProvider 등)가 토큰 발급/요청 로직을 각자 중복 구현하지 않도록
분리했다. KIS는 토큰 발급을 하루 1회 정도로 제한 권장하므로, 여러 어댑터가 이 세션
객체를 공유해야 불필요한 재발급을 피할 수 있다.

이 파일의 요청 쓰로틀링/재시도/hashkey/토큰 디스크 캐시 설계는, 같은 계정(kimjaylab)의
`claude` 저장소 `claude/ai-trading-bot-kiwoom-kagv8a` 브랜치에 있던 별도 프로젝트
(trading_bot/kis_client.py, 역매공파 스윙매매 봇)를 참고해 보강했다 - 그 구현이
KIS 공식 GitHub 레퍼런스를 근거로 hashkey/쓰로틀링을 이미 검증해뒀기 때문이다.

*** 이 클래스는 이 개발 환경(네트워크/실계좌 접근 불가)에서 실거래 검증을 거치지 않았다. ***
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

# "초당 거래건수를 초과하였습니다" - TR별 초당 호출 제한 초과 시 KIS가 내려주는 코드.
# 모의투자 계좌는 이 제한이 더 엄격해서, 요청 간 최소 간격을 둬도 종종 발생할 수 있다.
RATE_LIMIT_CODE = "EGW00201"


class KISAPIError(RuntimeError):
    pass


class KISSession:
    REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"
    VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        use_virtual: bool = True,
        http: requests.Session | None = None,
        request_interval_sec: float = 1.05,
        token_cache_path: Path | str | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.use_virtual = use_virtual
        self.domain = self.VIRTUAL_DOMAIN if use_virtual else self.REAL_DOMAIN
        self.http = http or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

        # KIS는 TR별로 초당 호출 횟수를 제한한다(모의투자가 더 엄격, EGW00201로 거절됨).
        # 매 요청 전 최소 간격을 강제해 예방하고, 그래도 걸리면 지수 백오프로 재시도한다.
        self._request_interval_sec = request_interval_sec
        self._last_request_at = 0.0

        # 토큰을 프로세스 재시작 후에도 재사용해 불필요한 재발급을 피한다.
        self._token_cache_path = Path(token_cache_path) if token_cache_path else Path(
            f".kis_token_cache_{'virtual' if use_virtual else 'real'}.json"
        )

    # ---------- 요청 쓰로틀링/재시도 ----------
    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_at
        wait = self._request_interval_sec - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        max_retries: int = 5,
    ) -> requests.Response:
        """요청 간 최소 간격을 강제하고, 초당 호출 제한(EGW00201) 발생 시 지수 백오프로 재시도한다.

        브로커/시세공급자는 requests.Session을 직접 쓰지 말고 이 메서드를 통해서만
        KIS 서버에 호출해야 한다 - 그래야 쓰로틀링/재시도가 일관되게 적용된다.
        """
        resp: requests.Response | None = None
        for attempt in range(max_retries + 1):
            self._throttle()
            resp = self.http.request(method, url, headers=headers, params=params, json=json_body, timeout=10)
            if resp.status_code == 200:
                return resp
            if RATE_LIMIT_CODE in resp.text and attempt < max_retries:
                wait = min(1.5 * (2 ** attempt), 20.0)
                time.sleep(wait)
                continue
            raise KISAPIError(f"HTTP {resp.status_code}: {resp.text}")
        raise KISAPIError(f"재시도 횟수 초과: {resp.text if resp is not None else ''}")

    # ---------- 인증 ----------
    def _load_cached_token(self) -> bool:
        if not self._token_cache_path.exists():
            return False
        try:
            data = json.loads(self._token_cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        if data.get("app_key") != self.app_key:
            return False
        if data.get("expires_at", 0) - 60 <= time.time():
            return False
        self._access_token = data["access_token"]
        self._token_expires_at = data["expires_at"]
        return True

    def _save_token_cache(self) -> None:
        try:
            self._token_cache_path.write_text(
                json.dumps(
                    {
                        "app_key": self.app_key,
                        "access_token": self._access_token,
                        "expires_at": self._token_expires_at,
                    }
                )
            )
        except OSError:
            pass  # 캐시 저장 실패는 치명적이지 않다 (다음 실행 시 재발급될 뿐)

    def ensure_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        if not force_refresh and self._load_cached_token():
            return self._access_token  # type: ignore[return-value]

        resp = self.request(
            "POST",
            f"{self.domain}/oauth2/tokenP",
            json_body={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
        )
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 86400))
        self._save_token_cache()
        return self._access_token

    def get_hashkey(self, body: dict[str, Any]) -> str:
        """주문 body의 위변조 방지 해시. KIS는 매수/매도 주문 시 이 값을 헤더에 요구한다."""
        resp = self.request(
            "POST",
            f"{self.domain}/uapi/hashkey",
            headers={
                "content-type": "application/json",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            json_body=body,
        )
        return resp.json()["HASH"]

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
