# 활성 전략 추적

> **이 문서의 목적**: 코인봇은 실험적으로 매매 전략과 파라미터를 자주 변경합니다. 다음 세션의 Claude(또는 본인)가 *지금 어떤 알고리즘이 돌고 있는지 / 왜 / 백테스트 결과 어땠는지*를 빠르게 파악하기 위한 단일 진입점.
>
> **갱신 규칙**: 전략 / 파라미터 / 활성 옵션 변경 시 이 문서를 같은 PR에서 갱신할 것. CLAUDE.md에 명시.

---

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

KIS US 룰: ORB 5분, 30분 형성, OR_low 손절, EOD 마감 10분 전 강제 청산, 트레일링 끔 (Zarattini 정통).
