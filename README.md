# kr-stock-daytrading-ai

국내 주식 장중 시간대별 전략 전환 AI 단타 자동매매 시스템.

단순히 규칙을 실행하는 프로그램이 아니라, (1) 하드 필터로 매매 불가 종목을 제거하고,
(2) 18개 판단 요소를 가중치 기반 스코어링으로 종합해 진입 여부를 판단하며,
(3) 백테스트 결과로 조건별 승률/기대수익을 검증하고, (4) ML(로지스틱회귀 + L1)로
가중치를 지속 학습해 불필요한 조건은 자동으로 0에 가깝게 수축시키는 구조로 만들었다.

## 왜 이렇게 설계했는가

- **전략/스코어링/리스크/브로커를 서로 분리**했다. 스코어가 아무리 높아도 리스크 매니저가
  거부하면 진입할 수 없고, 하드 필터에 걸리면 스코어 계산 자체를 하지 않는다. 책임을
  나눠야 "왜 이 종목을 샀는지/안 샀는지"를 설명할 수 있고, 조건별 검증도 가능하다.
- **백테스트/페이퍼트레이딩/실거래가 동일한 판단 로직(`BacktestEngine.step`)을 공유**한다.
  백테스트에서 검증한 필터·전략·리스크 로직이 실거래에서 다르게 동작할 위험을 없앴다.
- **모든 피처를 `[-1, 1]`로 정규화**해 가중치가 순수하게 "중요도"만 의미하도록 했다.
  이 덕분에 수작업 가중치 튜닝과 ML(로지스틱회귀 계수)이 같은 스케일에서 호환된다.
- **실데이터/실계좌 없이 검증 가능하도록** `MarketDataProvider`/`BrokerClient`를
  추상화했다. 이 개발 환경은 KRX 실시간 시세나 증권사 실계좌에 접근할 수 없으므로,
  프레임워크 정확성은 의도된 시나리오를 가진 합성 데이터(`SyntheticDataProvider`)로
  검증했다. **실거래 전 반드시 실데이터/모의투자 계좌로 재검증이 필요하다.**

## 아키텍처

```
config/
  config.yaml     # 장 시간대, 필터 임계값, 리스크 한도, 전략 파라미터, 국면별 보정치
  weights.json    # 스코어링 가중치 (ML 최적화가 갱신)

src/trading/
  data/           # MarketSnapshot, MarketDataProvider 인터페이스
                  # + SyntheticDataProvider(검증용 합성데이터), CSVDataProvider(실데이터 연동)
                  # + KISMarketDataProvider(국내 실시간 시세), KISOverseasMarketDataProvider(해외, 분봉없음)
  indicators/     # SMA, 일목균형표 기준선, ATR, ADX, 거래량비율, 캔들강도, 분봉추세 기울기
  scoring/        # FeatureExtractor(18개 피처 추출) + ScoringEngine(가중합 -> sigmoid 확률)
  market_regime/  # 지수 기반 상승장/하락장/횡보장 분류 -> 진입 임계값 보정
  filters/        # 매수 제외 하드필터 (유동성/스프레드/과열/변동성/뉴스성급등/관리종목 등)
  risk/           # 종목당·일일 손실한도, 연속손실, 재진입 쿨다운, 동시진입 제한,
                  # 최대 보유종목수, 손익비 필터, 연속손실 시 사이즈 축소
  strategies/     # PhaseSelector(경과시간 기반 전략전환) + 돌파/눌림목/추세눌림목 전략
  brokers/        # BrokerClient 인터페이스 + PaperBroker(시뮬레이션)
                  # + KISSession(공용 인증/쓰로틀링/hashkey) + KISBroker(국내)
                  # + KISOverseasBroker(해외) + kis_exchange_codes.py(거래소코드 매핑)
  backtest/       # 이벤트기반 백테스트 엔진, 성과지표, 조건별(피처별) 승률/기대수익 리포트
  ml/             # 트레이드 로그 -> 데이터셋 -> L1 로지스틱회귀로 가중치 재학습(불필요 조건 제거)
  execution/      # 실시간 루프 (백테스트와 동일한 step() 로직 재사용)

scripts/
  run_backtest.py      # 합성 데이터로 백테스트 실행 + 성과/조건별 리포트 출력
  optimize_weights.py  # 백테스트 트레이드 로그로 가중치 재학습 -> config/weights.json 갱신
  run_live.py          # 실시간 루프 실행 (페이퍼 / KIS 모의투자 / KIS 실전)

tests/            # pytest 단위테스트 (21개) - 스코어링/필터/리스크/전략/국면/백테스트/ML
```

## 국내장(KRX) / 미국장(US) 동시 지원

`config/config.yaml`의 `markets`에 `KRX`(09:00~15:30 KST)와 `US`(22:30~05:00 KST,
자정을 넘기는 세션)가 정의되어 있고, `Config(market="US")` 또는 스크립트의 `--market US`로
선택한다. 미국장은 자정을 넘기므로 `utils/time_utils.py`의 day-rollover 인식 함수
(`minutes_since_open`, `minutes_until`, `is_within_session`)로만 세션 경계를 판정하도록
전체 로직을 맞췄다 - 단순 `ts.time() >= X` 비교는 자정을 넘는 세션에서 깨진다.

```bash
python scripts/run_backtest.py --market US --symbols 24
```

**주의**: `US` 프로필의 시각은 미 동부 서머타임(EDT) 기준이다. 서머타임이 끝나면(11월경)
`config.yaml`에서 1시간씩 수동 조정(22:30→23:30, 05:00→06:00)해야 한다 - 자동 계산은
구현하지 않았다. 또한 VI/프로그램순매수/외국인·기관수급 피처는 KRX 특유의 데이터로,
미국주식에는 동일한 실시간 피드가 없다. `CSVDataProvider`로 미국주식 데이터를 공급할 때
해당 컬럼을 비워두면 자동으로 중립값(0 또는 False)으로 처리되지만, 그만큼 해당 피처의
설명력은 낮아진다는 점을 감안할 것. `KISBroker`는 국내주식 주문만 구현되어 있고,
미국주식 주문은 완전히 다른 API 엔드포인트/TR_ID가 필요해 별도 구현이 필요하다
(`brokers/kis_broker.py` 상단 주석 참고). 가중치(`weights.json`)는 현재 마켓 구분 없이
공유되므로, 두 시장의 트레이드 로그를 합쳐서 학습한다.

## 전략 전환 (시가 기준 경과 시간)

| 구간 | 전략 | 핵심 로직 |
|---|---|---|
| 0~10분 | 돌파매매 | 직전고점/시초가 실제 돌파 + 강한 양봉 확인 후 진입, 목표 2~3%, 돌파실패/체결강도 약화 시 즉시 청산 |
| 10~30분 | 눌림목매매 | 첫 상승(시가대비 1.5%↑) 확인 후 거래량 감소하는 '건강한 눌림' + 5일선/기준선 근접 + 분봉 반등 확인, 목표 5~10% |
| 30분~마감 | 추세+눌림목 | 5일선/기준선 위 우상향 추세 유지 + 거래량 생존 + 눌림 후 양봉, 추세훼손/기준선이탈/마감 10분 전 강제청산 |

`config/config.yaml`의 `phases`에서 경계값을 조정할 수 있다.

## 진입 판단에 쓰이는 18개 피처 (`scoring/features.py`)

거래량급증, 거래대금순위, 체결강도, 호가잔량불균형, 프로그램순매수, 외국인/기관순매수,
캔들의힘, 분봉추세, 일봉위치(5일선/기준선), 전고점돌파여부, VI발생여부, 섹터강도,
테마강도, 지수방향, 변동성, 평균거래대금, 스프레드, 유동성.

모두 `[-1, 1]`로 정규화 후 `config/weights.json`의 가중치와 내적해 `sigmoid`로 0~1
진입 확률을 계산한다(`scoring/engine.py`). 가중치는 `default` + 전략별 보정치(`breakout`/
`pullback`/`trend_pullback`) 구조라, 같은 피처라도 전략마다 중요도가 다르게 반영된다.

## 매수 제외 하드필터 (`filters/exclusion.py`)

스코어와 무관하게 즉시 제외: 관리종목/투자경고 플래그, 평균/당일 거래대금 부족,
스프레드 과다, 호가 잔량 과소, 시가 대비 과열, ATR 기준 변동성 과다, 거래량 뒷받침
없는 단독 급등(뉴스성 의심), (추세매매 구간 한정) ADX 기준 추세 부재.

## 리스크 관리 (`risk/manager.py`)

종목당 손절 한도, 일일 최대 손실(도달 시 당일 매매 전면 중단), 연속손실 한도(도달 시
중단), 연속손실 2회부터 다음 진입 사이즈 축소, 동일 종목 재진입 쿨다운, 5분당 신규
진입 건수 제한, 최대 동시 보유 종목 수, 손익비 최소 기준 미달 시 진입 차단.

## 시장 국면 분류 (`market_regime/classifier.py`)

지수 5MA/20MA 교차 + 추세 강도로 상승장/하락장/횡보장을 분류하고, `config.yaml`의
`regime.threshold_adjust`에 따라 하락장에서는 진입 임계값을 높이고(더 보수적),
상승장에서는 낮춘다(더 공격적).

## 백테스트 & 조건별 검증 (`backtest/`)

```bash
pip install -e .
python scripts/run_backtest.py --symbols 24 --seed 42 --save-trades trades.csv
```

`feature_condition_report()`가 각 피처를 3분위(저/중/고)로 나눠 구간별 승률과 평균
수익률을 계산한다 - "이 조건이 실제로 승률에 기여하는가"를 데이터로 검증하기 위함이다.
`exit_reason_report()`/`exclusion_report()`로 청산 사유와 제외 사유 분포도 확인할 수 있다.

## ML 가중치 최적화 (`ml/optimizer.py`)

```bash
python scripts/optimize_weights.py --symbols 40 --dry-run   # 파일 변경 없이 결과만 확인
python scripts/optimize_weights.py --symbols 40             # config/weights.json 갱신
```

L1 정규화 로지스틱회귀로 승/패를 예측하는 계수를 학습한다. L1은 기여도가 낮은 피처의
계수를 자연스럽게 0으로 수축시키므로 "불필요한 조건 제거"가 수작업이 아니라 데이터
기반으로 이루어진다. 표본이 적은 전략(phase)은 과적합을 피하기 위해 공통 가중치만
사용하고, 표본이 충분할 때만 전략별 보정치를 별도 학습한다.

## 실데이터/실거래 연동 방법

### 데이터
`data/csv_provider.py`의 `CSVDataProvider`가 기대하는 디렉토리 구조에 맞춰 분봉/일봉
CSV를 준비하면 `SyntheticDataProvider` 대신 그대로 교체할 수 있다(전략/스코어링/리스크
코드는 수정할 필요 없음). 실시간 시세는 `data/kis_provider.py`의 `KISMarketDataProvider`
(국내)와 `data/kis_overseas_provider.py`의 `KISOverseasMarketDataProvider`(해외)가
KIS REST API로 조회한다.

### 브로커
`brokers/kis_broker.py`(국내주식)와 `brokers/kis_overseas_broker.py`(미국 등 해외주식)에
한국투자증권 Open API 어댑터를 마련해뒀다. 인증(토큰 발급/헤더/hashkey)과 요청
쓰로틀링·재시도는 `brokers/kis_session.py`의 `KISSession`으로 공유한다 - 브로커와
시세 공급자가 같은 세션을 공유해 토큰 재발급을 최소화하고, TR별 초당 호출 제한
(`EGW00201`)에 걸리면 지수 백오프로 자동 재시도하며, 토큰은 디스크에 캐시해
프로세스를 재시작해도 불필요한 재발급을 하지 않는다. 주문 전에는 `KISSession.get_hashkey()`로
요청 위변조 방지 해시를 받아 헤더에 포함한다.

> 이 인증/쓰로틀링/hashkey 설계와 해외주식 TR_ID(`TTTT1002U`/`TTTT1006U`)는 같은 계정
> (kimjaylab)의 `claude` 저장소 `claude/ai-trading-bot-kiwoom-kagv8a` 브랜치에 있던
> 별도 프로젝트(KIS 공식 GitHub 레퍼런스를 근거로 작성됨)를 참고해 보강한 것이다.
> 그 프로젝트는 미국주식 스윙매매용 완전히 다른 전략("역매공파")을 쓰는 별개의 봇이라
> 전략/유니버스 로직은 가져오지 않았고, KIS 연동 부분만 참고했다.

**신뢰도가 다르다는 점을 알아야 한다**:
- 국내주식 잔고조회(`TR VTTC8434R`/`TTTC8434R`, 예수금·보유종목 모두 포함)는 실사용자
  모의투자 계좌로 **실제 호출 검증을 마쳤다**(2026-07). `get_cash_balance()`가 한때 쓰던
  별도 TR `VTTC8908R`은 실제로는 "매수가능조회"(종목코드+가격 필요)용이라 에러가 났던
  것으로 확인되어 제거했다 - 예수금은 잔고조회 응답에 이미 포함되어 있었다.
- 국내주식 주문(`KISBroker.place_order`)의 TR_ID(`TTTC0802U` 등)는 문서상 확신도는 높지만
  실제 주문 체결까지는 아직 검증하지 못했다 - 소액으로 먼저 테스트할 것.
- 해외주식 주문(`KISOverseasBroker`)의 TR_ID(`TTTT1002U`/`TTTT1006U`, 모의는 `V`로 치환)는
  위 참고 프로젝트를 근거로 정정했지만, 이 환경에서 KIS 공식 문서와 대조하거나 실제
  호출로 검증하지는 못했다 - 여전히 확신도가 국내주식보다 낮다.
- 해외주식 거래소 코드는 주문/잔고(trading) API와 시세(quotations) API가 서로 다른
  코드 체계(4자리 vs 3자리)를 쓴다고 알려져 있다 - `brokers/kis_exchange_codes.py`에서
  캐노니컬 이름("NASDAQ"/"NYSE"/"AMEX")으로 통일해뒀지만, 실제 값 자체는 검증 필요.
- `KISMarketDataProvider`(국내)는 현재가/호가/분봉/일봉만 구현했고, `KISOverseasMarketDataProvider`
  (해외)는 현재가/일봉만 구현했다(**분봉 데이터 자체가 없어** 폴링 시점의 현재가를 1개
  봉으로 근사한다). 두 공급자 모두 프로그램매매·외국인/기관 수급·VI·섹터/테마 피처는
  대응 엔드포인트를 확인하지 못해 중립값(0/False)으로 채운다. 즉 실거래에서는 이
  피처들의 실질적인 영향력이 사라진다 - ML 최적화가 자연히 가중치를 낮추긴 하겠지만,
  정확한 판단을 원하면 별도 데이터 소스 연동이 필요하다.
- 국내/해외 모두 `get_positions()`가 반환하는 포지션에는 진입시각/전략명/손절가/목표가가
  없다(KIS 잔고조회 응답 자체에 없는 정보). 정상 운영 중에는 `RiskManager`와
  `BacktestEngine._entry_context`가 이 정보의 1차 소스이며, `get_positions()`는 장애
  복구·검증용 보조 수단으로만 쓸 것.
- 실시간 루프(`LiveRunner`)는 시장 국면(상승/하락/횡보) 판단에 쓰는 지수 시계열을
  아직 실시간으로 공급받지 않는다 - `index_close`를 넘기지 않으면 항상 횡보장으로
  간주해 임계값을 보정한다. 지수 데이터를 실시간으로 붙이려면 KOSPI/코스닥 지수 조회
  API를 추가로 연동해 매 틱마다 시리즈에 append 해야 한다.

### 실행

```bash
# 1) 페이퍼트레이딩(합성 데이터)로 로직만 확인
python scripts/run_live.py --broker paper --market KRX

# 2) KIS 모의투자 계좌 연결 (환경변수로 자격증명 전달, 코드에 하드코딩 금지)
export KIS_APP_KEY=...
export KIS_APP_SECRET=...
export KIS_ACCOUNT_NO=...
python scripts/run_live.py --broker kis --market KRX --watchlist 005930,000660 --virtual

# 3) 충분히 검증한 뒤에만 실전 도메인 사용 (기본값은 항상 모의투자)
python scripts/run_live.py --broker kis --market KRX --watchlist 005930,000660 --no-virtual

# 4) 미국장 (해외 시세는 분봉이 없어 국내보다 신뢰도가 낮음 - 위 안내 참고)
python scripts/run_live.py --broker kis --market US --watchlist AAPL,IBM --exchange-map AAPL:NASDAQ,IBM:NYSE --virtual
```

`--market US --broker kis`는 `KISOverseasBroker`(주문)와 `KISOverseasMarketDataProvider`
(시세)를 함께 사용한다. `--exchange-map`을 생략하면 모든 종목을 NASDAQ으로 가정한다.

`execution/live_runner.py`의 `LiveRunner`는 백테스트와 동일한 `BacktestEngine.step()`을
재사용해 매 분 한 번씩 판단을 반복한다. 이 구조 덕분에 브로커/데이터 공급자만
교체하면 되고, 필터·스코어링·전략·리스크 로직은 백테스트에서 검증한 것과 완전히 동일하게
동작한다.

## 한계 및 주의사항

- 합성 데이터(`SyntheticDataProvider`)는 프레임워크 로직 검증용이며, 실제 시장의 미시구조
  (호가 움직임, 체결강도, 프로그램/외국인 수급의 실제 패턴)를 정확히 재현하지 않는다.
  실거래 적용 전 반드시 실데이터로 백테스트를 재실행하고 조건별 리포트를 다시 검증할 것.
- `KISBroker`/`KISOverseasBroker` 모두 주문 체결가 확인(시장가 주문 직후 실제 체결가)은
  별도 체결통보 API 연동이 필요해 미구현 상태다 - 현재는 주문을 넣은 시점에 `PENDING`
  상태로만 반환한다.
- 이 시스템은 투자 조언이 아니며, 실거래 손실에 대한 책임은 사용자에게 있다.

## 테스트

```bash
pip install -r requirements.txt
python -m pytest -q
```
