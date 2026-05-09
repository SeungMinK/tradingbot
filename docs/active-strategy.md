# 활성 전략 추적

> **이 문서의 목적**: 봇 전략과 파라미터가 자주 변경됩니다. 다음 세션의 Claude(또는 본인)가 *지금 어떤 알고리즘이 돌고 있는지 / 왜 / 백테스트 결과 어땠는지*를 빠르게 파악하기 위한 단일 진입점. 봇별로 섹션 분리.
>
> **갱신 규칙**: 전략 / 파라미터 / 활성 옵션 변경 시 이 문서를 같은 PR에서 갱신할 것. CLAUDE.md에 명시.

---

# 🪙 코인봇 (Upbit)

## 현재 활성 전략

**`vwap_orb_breakout`** — Option 1 (#360, 2026-05-09)

### 핵심 파라미터

| 항목 | 값 | env 오버라이드 |
|---|---|---|
| ORB 시작 | KST 22:00 | `COIN_ORB_HOUR_KST` |
| ORB 형성 | 60분 (22:00~23:00) | `extra.orb_minutes` |
| 진입 윈도우 | 23:00~04:00 (5h) | `COIN_ENTRY_WINDOW_HOURS` |
| EOD 청산 | 다음날 KST 11:00 | `COIN_EOD_HOUR_KST` |
| 봉 단위 | 15분봉 | `extra.bar_minutes` |
| 거래량 spike | 1.5x cumulative | `extra.volume_spike_multiplier` |
| 손절 | OR_low 또는 −5% | `params.stop_loss_pct` |
| 트레일링 | 고점 −3% (실질 수익 시) | `params.trailing_stop_pct` |
| 화이트리스트 | BTC/ETH/XRP/SOL/ADA/DOGE/AVAX/LINK | `bot_config.coin_whitelist` |

### 매매 흐름

```
KST 22:00 ─── ORB 형성 시작 (1h)
KST 23:00 ─┬─ ORB 확정 (or_high, or_low)
           │   진입 평가 시작
           │   조건: 가격 > or_high AND 가격 > VWAP AND 거래량 ≥ 1.5x
KST 04:00 ─┴─ 진입 마감 (이후 신규 진입 X)

진입 시 ─── 손절가 = or_low / 트레일링 −3% / EOD 11:00 강제 청산

KST 11:00 ─── EOD: 보유 코인 전부 강제 매도
```

### 백테스트 결과 (2026-05-09 시점, 8 화이트리스트 × ~3주)

| 지표 | 값 |
|---|---|
| 거래 수 | 10건 |
| 승률 | **80.0%** |
| 평균 P&L | **+1.87%** |
| Best | +5.66% |
| Worst | −2.17% |
| 복리 | **+20.63%** |

⚠️ 표본 작음 (10건). 7일 운영 후 재평가 예정.

---

## 변경 이력

| 날짜 | PR | 변경 | 사유 |
|---|---|---|---|
| 2026-05-09 | #360 | Option 1 채택 (ORB 22 / 진입 5h / EOD 11) | 3차원 sweep에서 +20.63% 복리 1위 |
| 2026-05-09 | #357 | `check_sell` roi_table 우회 | 실거래 8건 100%가 +0.8% 룰에 잘림 |
| 2026-05-09 | #359 (closed) | EOD 09:00 → 06:00 (보류) | Option 1로 supersede |
| 2026-05-08 | #321 | vwap_orb_breakout 전략 신규 (ORB 0 / EOD 9) | Zarattini 코인 적용 가설 |

---

## 후보 옵션 (백테스트 결과)

다음 평가 시점(7일 후)에 같은 백테스트 스크립트로 재실행해서 비교.

| Option | ORB | 진입 | EOD | 거래 | 승률 | 평균 | 복리 | 비고 |
|---|---|---|---|---|---|---|---|---|
| **1 (현재)** | **22:00** | **5h** | **11:00** | 10 | **80.0%** | **+1.87%** | **+20.63%** | US 개장 활용 |
| 2 | 00:00 | 5h | 06:00 | 11 | 72.7% | +1.40% | +16.29% | dead time EOD |
| 3 | 10:00 | 4h | 20:00 | 8 | 75.0% | +1.94% | +16.20% | KR 개장 |
| 4 | 00:00 | 2h | 11:00 | 7 | 100% | +2.17% | +16.12% | 짧은 진입(표본 작음) |

### 옵션별 강점/약점

- **Option 1**: 복리 1위, 거래량 1위 시간대(KST 22:00) 활용. EOD 11:00 슬리피지 보통(1.02x).
- **Option 2**: EOD가 dead time(0.67x)이라 슬리피지 최저. 다만 복리 -4%p.
- **Option 3**: KR retail 모멘텀. 평균 P&L 1위. 표본 8건만.
- **Option 4**: 100% 승률(7/7) 매력적이지만 표본 작음. 짧은 진입 윈도우 = 시그널 못 잡으면 패스.

---

## 다음 평가 일정

- **2026-05-16 (Option 1 운영 7일 후)**: 실거래 데이터로 옵션 1/2/3/4 재백테스트
- 사용 스크립트:
  - `scripts/backtest_optimal_combo.py` — 3차원 sweep
  - `scripts/backtest_22orb_eod_sweep.py` — Option 1 EOD 미세 튜닝
  - `scripts/backtest_entry_window.py` — 진입 윈도우 길이 비교
  - `scripts/analyze_volume_by_hour.py` — 시간대별 거래량 패턴
  - `scripts/sweep_orb_volume_threshold.py` — 거래량 임계값 sweep

---

## 데이터 출처

- **실거래 매매**: `trades` 테이블 (strategy='vwap_orb_breakout', market='upbit')
- **시그널 평가 이력**: `trade_signals` 테이블 (skip_reason 포함)
- **분봉 OHLCV**: `ohlcv_minutes` 테이블 (8 화이트리스트 코인)
- **봇 설정**: `bot_config` 테이블 (`coin_whitelist`, `roi_table` 등)

---

## 참고 — KIS 미국주식 봇 (별도)

KIS US 봇은 **별도 sell 경로**(`src/cryptobot/bot/kis_strategy.py:evaluate_sell`)를 사용. `BaseStrategy.check_trailing_stop` 및 `roi_table` 영향 없음. Option 1 변경의 영향도 받지 않음.

상세는 아래 *KIS 미국주식 봇* 섹션 참조.

---

# 🇺🇸 KIS 미국주식 봇

## 현재 활성 전략

**`zarattini_3x_atr`** — Pure Zarattini 3X 변형 (논문 TQQQ 변형, #364, 2026-05-09)

> 백테스트(논문): TQQQ 2016~2023 **+9,350% / 연환산 알파 93%** (3X 레버리지 최적화)

### 핵심 파라미터

| 항목 | 값 | env 오버라이드 |
|---|---|---|
| 종목 페어 | **SOXL/SOXS** (반도체 강세 3X / 약세 3X) | `kis_us_symbols.enabled` DB |
| 봉 | 5분봉 | `KIS_US_OHLCV_INTERVAL` |
| OR 형성 | 첫 5분봉 (NY 09:30~09:35) | — |
| 진입 룰 | 첫 봉 양봉 → 종목 매수 / 음봉/도지 → skip | — |
| 도지 임계 | 0.05% (몸통 비율) | `KIS_US_DOJI_THRESHOLD_PCT` |
| **손절** | **0.05 × ATR(14d)** (절대가, 14일 변동성에 적응) | `KIS_US_ATR_STOP_PCT`, `KIS_US_ATR_PERIOD` |
| **익절** | **없음 — EOD까지 hold** (큰 추세 끝까지 잡음) | — |
| EOD | NY 15:50 (마감 10분 전) | `KIS_US_FORCE_SELL_BEFORE_CLOSE_MIN` |
| 사이징 | 1% 리스크/거래 | `KIS_US_RISK_PCT` |
| 페어 mutex | SOXL ↔ SOXS 동시 보유 X | `ZARATTINI_PAIRS` 상수 |

### 왜 3X 변형이 baseline(bar1+10R TP)보다 좋은가 (논문 근거)

- 3X ETF는 일일 변동성이 큼 → bar1 low 손절은 좁아 가짜 stop-out 빈발
- 0.05 × ATR(14) 손절 = 14일 변동성에 적응 (여전히 tight하지만 noise 흡수)
- No TP = 큰 모멘텀 날 EOD까지 hold (10R 제한 X)
- 결과: TQQQ 베이스라인(+1,484%) → 3X 최적화(+9,350%) 6배 개선

### 핵심 로직 (논문 그대로)

```
NY 09:30 ─── SOXL/SOXS 첫 5분봉 동시 형성 시작
NY 09:35 ─── 둘째 봉 시작 = 진입 평가 시점
        │
        ├─ SOXL bar1 양봉 → SOXL 매수 (반도체 상승 베팅)
        ├─ SOXS bar1 양봉 → SOXS 매수 (반도체 하락 베팅, 효과적 숏)
        └─ 둘 다 도지 → 그날 매매 X (논문 정신)

진입 시: 손절가 = bar1 low / 익절가 = entry + 10R / EOD = 15:50
페어 mutex: SOXL/SOXS 둘 다 동시 매수 X (mirror라 자연 방지 + 명시 체크)
```

### 운영 자본

400,000 KRW (~$290 USD). 1% 리스크/거래 = $2.90.

### 변경 이력 (KIS US)

| 날짜 | PR | 변경 | 사유 |
|---|---|---|---|
| 2026-05-09 | #364 (3X-ATR) | **Pure Zarattini 3X 변형 채택** (ATR 손절, No TP) | 논문 TQQQ 변형 +9,350% 알파 |
| 2026-05-09 | #364 (baseline) | Pure Zarattini Bar-1 baseline 추가 (10R TP) | 논문 baseline (+1,484%), 3X-ATR 도입 전 단계 |
| 2026-05-08 | #305 | Zarattini ORB 모드 추가 (SOXL only) | 논문 80% 충실 — 진입 트리거 + 양방향 갭 |

### 운영 모드 전환 (env)

| 모드 | env | 백테스트 (논문) |
|---|---|---|
| **3X 최적 (현재)** | `KIS_US_STRATEGY=zarattini_3x_atr` | **+9,350% / α 93%** |
| Baseline | `KIS_US_STRATEGY=zarattini_bar1` | +1,484% / α 50% |
| 기존 혼합 | `KIS_US_STRATEGY=breakout` | (벤치마크) |

### 데이터 출처

- 실거래 매매: `trades` 테이블 (`market='kis_us'`, `strategy='kis_us_zarattini_bar1'`)
- 매 틱 평가: `kis_us_evaluations` 테이블 (bar1 양봉/음봉/도지 reason)
- 분봉 OHLCV: KIS API (실시간) — DB 저장 없음. 백테스트는 별도 분봉 수집 필요

### 후속 (별개 일감)

- TQQQ/SQQQ 분봉 KIS API 가용성 검증 → 가능 시 페어 추가
- KIS API 5분봉 히스토리 수집 → retrospective 백테스트 가능
- 1% 리스크 외 다른 사이징(0.5%/2%) sweep
- 도지 임계 0.05% 외 변형 sweep

### 논문 자료

- **원문 (SSRN PDF)**: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622
- **The Robust Trader 한국어식 정리**: https://therobusttrader.com/can-day-trading-really-be-profitable-rules-backtest-statistics-performance-analysis/
- **이슈 #364 코멘트**에 핵심 사양 / 검증 결과 / 도지 처리 / 우리 구현 차이 정리됨
