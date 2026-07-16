"""YAML 설정 및 가중치 JSON 로더."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class Phase:
    name: str
    start_offset_min: int
    end_offset_min: int


class Config:
    """config.yaml을 읽어 타입이 있는 값으로 노출한다.

    market 인자로 세션 프로필(KRX/US)을 선택한다. 필터/리스크/전략/가중치 설정은
    마켓과 무관하게 공용이며, 세션 시각(개장/마감/강제청산)만 마켓별로 분리된다.
    """

    def __init__(self, path: Path | str = DEFAULT_CONFIG_PATH, market: str = "KRX"):
        self.path = Path(path)
        with open(self.path, encoding="utf-8") as f:
            self._raw: dict[str, Any] = yaml.safe_load(f)
        if market not in self._raw["markets"]:
            raise ValueError(f"알 수 없는 market 프로필: {market} (사용 가능: {list(self._raw['markets'])})")
        self.market_name = market
        self._market_cfg = self._raw["markets"][market]

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    @property
    def market_open(self) -> time:
        return _parse_time(self._market_cfg["open_time"])

    @property
    def market_close(self) -> time:
        return _parse_time(self._market_cfg["close_time"])

    @property
    def force_liquidation_time(self) -> time:
        return _parse_time(self._market_cfg["force_liquidation_time"])

    @property
    def hard_close_time(self) -> time:
        return _parse_time(self._market_cfg["hard_close_time"])

    @property
    def phases(self) -> list[Phase]:
        return [Phase(**p) for p in self._raw["phases"]]

    @property
    def filters(self) -> dict[str, Any]:
        return self._raw["filters"]

    @property
    def risk(self) -> dict[str, Any]:
        return self._raw["risk"]

    @property
    def strategies(self) -> dict[str, Any]:
        return self._raw["strategies"]

    @property
    def regime(self) -> dict[str, Any]:
        return self._raw["regime"]

    def weights_path(self) -> Path:
        rel = self._raw["scoring"]["weights_path"]
        p = Path(rel)
        return p if p.is_absolute() else REPO_ROOT / p

    def load_weights(self) -> dict[str, Any]:
        with open(self.weights_path(), encoding="utf-8") as f:
            return json.load(f)


_config_cache: dict[str, Config] = {}


def get_config(market: str = "KRX") -> Config:
    if market not in _config_cache:
        _config_cache[market] = Config(market=market)
    return _config_cache[market]
