<p align="center">
  <img src="docs/images/logo.png" alt="TradingBot Logo" width="200" />
</p>

<h1 align="center">TradingBot</h1>

<p align="center">
  AI 기반 멀티 마켓 자동매매 시스템 (코인 + 한국주식 + 미국주식)
  <br />
  10개 매매 전략 + Claude AI 시장분석 + 멀티 종목 자동 선별
  <br />
  <a href="https://cryptobot-eight.vercel.app"><strong>Live Demo</strong></a> · <a href="https://api.seungmink.dev/api/docs"><strong>API Docs</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/React-18-61dafb?logo=react" alt="React" />
  <img src="https://img.shields.io/badge/Claude_AI-Haiku_4.5-blueviolet?logo=anthropic" alt="Claude" />
  <img src="https://img.shields.io/badge/Exchanges-Upbit_+_KIS-blue" alt="Exchanges" />
  <img src="https://img.shields.io/badge/tests-474_passed-brightgreen" alt="Tests" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
</p>

<p align="center">
  <img width="100%" alt="Dashboard" src="https://github.com/user-attachments/assets/df01385b-54c6-4189-9172-1fe620b2b839" />
</p>

> **면책 조항**: 이 프로젝트는 교육 및 연구 목적입니다. 실제 투자에 사용할 경우 손실이 발생할 수 있으며, 개발자는 이에 대한 책임을 지지 않습니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **10개 매매 전략** | 볼린저+RSI 복합, 변동성 돌파, MACD, 이동평균 교차 등 (시장 상태별 자동 선택) |
| **AI 시장분석** | Claude Haiku가 4시간마다 뉴스 분석 → 전략/파라미터 자동 조절 (~월 640원) |
| **멀티코인** | 거래량/변동성 기반 자동 선별 (BTC/ETH/XRP 고정 + 알트코인 자동) |
| **뉴스 수집** | CoinDesk, CoinTelegraph RSS + Fear & Greed Index (30분 주기) |
| **리스크 관리** | 수수료 가드, 손절/트레일링 스탑, AI 하드 리밋 |
| **프롬프트 버전 관리** | AI 판단 이력 + 프롬프트별 성과 추적 |
| **Admin 대시보드** | 8개 페이지 (React + TypeScript) |
| **에러 로깅** | 날짜별 분리 + AI 비용 트래킹 |

<details>
<summary><b>Admin 대시보드 상세</b></summary>

| 페이지 | 기능 |
|---|---|
| 대시보드 | KPI(자산/손익%), AI 시장 요약, 최근 매매, 모니터링 코인 |
| 매매 내역 | 시간순 매매 이력, 신뢰도, 순수익(수수료 포함) |
| 전략 관리 | 10개 전략 카드, 코인별 적용 현황, 파라미터 편집 + 시뮬레이션 |
| 매매 신호 | 실시간 신호 이력, 지표/파라미터 상세, 30초 자동 갱신 |
| 뉴스 | RSS 뉴스 + 공포/탐욕 지수, 감성 필터, 코인 검색 |
| 수익률 분석 | 승률, 매도 수익 합계, 총 수수료 |
| LLM 관리 | AI 모델/비용/프롬프트 히스토리, 분석 이력 |
| 설정 | 봇/리스크/알림/코인 설정 + LLM 하드 리밋 |

<img src="docs/images/strategies.png" alt="Strategies" width="100%" />

</details>

---

## 아키텍처

```
                    ┌─────────────────────────────────────┐
                    │        Vercel (Frontend)             │
                    │   cryptobot-eight.vercel.app         │
                    │   React 18 + TypeScript + Vite       │
                    └──────────────┬──────────────────────┘
                                   │ HTTPS
                    ┌──────────────┴──────────────────────┐
                    │   Cloudflare Tunnel (API Proxy)      │
                    │   api.seungmink.dev → localhost:8000 │
                    └──────────────┬──────────────────────┘
                                   │
┌──────────────────────────────────┴──────────────────────┐
│                    Local Machine                        │
│                                                         │
│  ┌─ Trading Bot ─────────────────────────────────────┐  │
│  │  Scanner → DataCollector (60초)                    │  │
│  │  StrategyRegistry (10개, 시장 상태별 자동선택)       │  │
│  │  RiskManager (수수료 가드 + 하드 리밋)              │  │
│  │  LLM Analyzer (4시간) → 파라미터 자동 조절          │  │
│  │  HealthChecker (매일) / WeeklyReporter / MonthlyAudit│ │
│  └───────┬───────────────────────────────────────────┘  │
│          │                                              │
│  ┌───────┴──────── SQLite (공유 DB) ─────────────────┐  │
│  │  market_snapshots │ trade_signals │ trades         │  │
│  │  ohlcv_daily      │ news_articles │ llm_decisions  │  │
│  │  prompt_versions  │ fear_greed    │ bot_config     │  │
│  └───────┬───────────┬───────────────────────────────┘  │
│          │           │                                  │
│  ┌───────┴─────┐ ┌───┴──────────────────────────────┐   │
│  │ News        │ │ FastAPI (API Server)              │   │
│  │ Collector   │ │ JWT Auth + Rate Limit             │   │
│  │ RSS + F&G   │ │ Security Headers                  │   │
│  │ (30분 주기)  │ │                                  │   │
│  └─────────────┘ └──────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 핵심 알고리즘 — Zarattini ORB 단타

학술 근거: Zarattini & Aziz (2023) ["Can Day Trading Really Be Profitable?"](https://www.semanticscholar.org/paper/Can-Day-Trading-Really-Be-Profitable-Evidence-of-in-Zarattini-Aziz/4d55f526cc56f08662cb8976796cd3b719ef6d2b)
QQQ 5분 ORB 백테스트 → **누적 +1,484%** (2016~2023, 8년) / 연환산 알파 33%.

### 한 줄 요약

> 장 시작 후 첫 N분의 고점/저점을 ORB로 잡고, 거래량 폭발 + VWAP 위 + ORB 돌파가 **동시에** 만족하면 매수. 손절은 OR_low 가격 도달, 익절은 추세 끝까지 트레일링 또는 EOD 강제 청산.

### 용어 사전

| 용어 | 영문 | 설명 |
|---|---|---|
| **ORB** | Opening Range Breakout | 장 시작 후 N분 고점/저점 돌파 매매 |
| **OR_high** | Opening Range High | 형성 시간 동안 최고가 (= 매수 돌파선) |
| **OR_low** | Opening Range Low | 형성 시간 동안 최저가 (= 손절선, 가변) |
| **VWAP** | Volume Weighted Average Price | 거래량 가중 평균가 = "오늘의 공정 가격" |
| **RVOL** | Relative Volume | 거래량 spike (평균 대비 N배) |
| **트레일링** | Trailing Stop | 고점 대비 X% 빠지면 매도 (이익 보호) |
| **EOD** | End of Day | 장 마감 → 모든 보유 강제 청산 |

### 매수 조건 (4중 모두 충족)

1. **ORB 돌파**: 가격 > OR_high
2. **VWAP 강세**: 가격 > VWAP (당일 거래량 가중 평균)
3. **거래량 spike**: RVOL ≥ 1.5x (코인) 또는 2.0x (미국주식)
4. **ORB 형성 완료**: 최소 N분 데이터 축적

### 매도 룰 (3가지 중 먼저 발동)

1. **손절 (가변)**: 가격 ≤ OR_low → 즉시 시장가 매도
2. **트레일링**: 고점 대비 -2% 빠짐 + 수익 중 → 매도
3. **EOD 청산**: 장 마감 시점 → 보유 종목 전부 매도

### 신뢰도 계산 (여러 종목 동시 신호 시 우선순위)

```
confidence =
  min(ORB 돌파폭 × 50, 0.4)      // 0~0.4
  + min(VWAP 거리 × 30, 0.3)     // 0~0.3
  + min((RVOL - 1) × 0.15, 0.25) // 0~0.25
= 0.0 ~ 0.95
```

자금 부족 시 confidence 높은 순으로 매수 → 다음 후보 자금 0이면 skip.

### 시장별 적용 차이

| 항목 | 미국주식 (KIS) | 코인 (Upbit) |
|---|---|---|
| 시장 시간 | NY 09:30~16:00 | 24/7 |
| ORB 형성 | 첫 5분 (09:30~09:35) | KST 자정 후 1시간 (00:00~01:00) |
| 봉 단위 | 5분봉 | 15분봉 (코인 변동성 커서 5분 노이즈 큼) |
| 거래량 임계 | 2.0x (학술 표준) | 1.5x (코인 특성 완화) |
| EOD | NY 15:50 (마감 10분 전) | KST 09:00 (사용자 정의) |
| 종목 풀 | 사용자 선택 (admin DB 토글) | 8종목 화이트리스트 |
| 거래 단위 | 1주 정수 (레버리지 ETF) | 소수점 가능 |

### 실전 시나리오 (SOXL 예시)

```
NY 09:30  장 열림 - OR 형성 시작
NY 09:35  OR 확정: high $165.41, low $165.05

NY 10:05  매수 신호!
  ✅ ORB 돌파: $168.63 > $165.41
  ✅ VWAP +2.04% 위
  ✅ 거래량 2.3x
  → 매수 $168.63 (1주), 손절선 $165.05

NY 10:30~14:00  보유 중 (트레일링 추적)
  $172 → 고점 갱신
  $175 → 고점 갱신
  $171.50 → 고점 -2% → 트레일링 매도
  결과: +1.7%

⚠️ 만약 $164.50 도달 시:
  → OR_low $165.05 아래 → 즉시 손절
  결과: -2.45%

NY 15:50  자고 있는데 보유 중이면:
  → EOD 강제 매도 (다음날 갭 위험 회피)
```

### 왜 4중 조건을 같이 봐?

- **ORB만**: 가짜 돌파 많음 (큰 자금 안 들어와서 다시 빠짐)
- **VWAP만**: 위에 있다고 더 오른다는 보장 X
- **거래량만**: 무엇 때문에 늘었는지 모름
- **3개 같이**: "큰 자금이 강세 방향으로 진짜 들어왔다" = 추세 시작 확인

### 매 틱 동작 흐름 (60초)

```
[가격/잔고/분봉 fetch]  ─ 캐시 히트 시 1초 미만
   │
   ▼
보유 중?
   │
   ├─ YES → [매도 평가] → 손절/트레일링/EOD 중 발동 시 매도
   │
   └─ NO → [매수 평가] → 4중 조건 → 충족 시 매수 큐
                                       ↓
                          신뢰도 정렬 → 자금 가능한 만큼 순차 매수
```

---

## 빠른 시작

### 사전 요구사항

- Python 3.11+
- Node.js 18+
- 업비트 API Key ([발급](https://upbit.com/mypage/open_api_management) — 출금 권한 제외)

### 설치

```bash
git clone https://github.com/SeungMinK/tradingbot.git
cd cryptobot

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd admin && npm install && cd ..
```

### 환경변수 설정

```bash
cp .env.example .env
```

필수:
```env
UPBIT_ACCESS_KEY=your-key        # 업비트 API
UPBIT_SECRET_KEY=your-secret
```

선택:
```env
SLACK_BOT_TOKEN=xoxb-xxx         # Slack 알림
SLACK_CHANNEL=#channel
ANTHROPIC_API_KEY=sk-ant-xxx     # Claude AI (없으면 알고리즘만 동작)
JWT_SECRET=your-secret           # Admin 로그인
```

### 실행

```bash
# 관리자 계정 생성
python scripts/create_admin.py

# 전체 실행 (봇 + API + 뉴스 수집기 + Admin)
make start
```

| 명령어 | 설명 |
|---|---|
| `make start` | 전체 실행 |
| `make bot` | 트레이딩 봇 |
| `make api` | API 서버 |
| `make web` | Admin 개발 서버 |
| `make news` | 뉴스 수집기 |
| `make test` | 테스트 (101건) |

---

## 기술 스택

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Trading | pyupbit (Upbit API, 현물) |
| Data | pandas, numpy, SQLite |
| LLM | Anthropic Claude Haiku 4.5 |
| API | FastAPI + uvicorn |
| Frontend | React 18 + TypeScript + Vite |
| Hosting | Vercel (Frontend) + Cloudflare Tunnel (API) |
| Domain | seungmink.dev (Cloudflare DNS) |
| News | RSS + Fear & Greed API |
| Notification | Slack Bot Token |
| Tests | pytest (101건) |

---

## 데이터 설계

모든 타임스탬프는 UTC. 매매 판단의 전체 흐름을 추적 가능하도록 설계.

```
뉴스 수집 (30분) → news_articles + fear_greed_index
     ↓
AI 분석 (4시간) → llm_decisions + prompt_versions
     ↓
시장 데이터 (60초) → market_snapshots + ohlcv_daily
     ↓
매매 판단 (60초) → trade_signals (전략 + 파라미터 JSON)
     ↓
주문 실행 → trades (수익률 + 수수료 포함)
     ↓
성과 평가 → llm_decisions.evaluation_was_good
```

---

## 개발 로드맵

### Phase 1: 자동매매 MVP (완료)

프로젝트 설정, DB, 지표, 10개 전략, 주문, 수집, 알림, 스케줄러

### Phase 2: Admin + AI 연동 ✅

FastAPI + React Admin 8페이지, 멀티코인, 볼린저+RSI 복합, 수수료 가드, 뉴스 수집, Claude AI 시장분석, 프롬프트 버전 관리, 통합 테스트 90건

### Phase 2.5: 수익률 개선 (진행 중)

[Freqtrade](https://github.com/freqtrade/freqtrade) · [Hummingbot](https://github.com/hummingbot/hummingbot) 벤치마킹

- [x] #81 시간 기반 ROI 테이블 — 보유 시간별 목표 수익 자동 조절
- [x] #82 코인별 쿨다운 + 손실 코인 잠금 — 연속 손실 방지
- [x] #83 스프레드 필터 — 호가 차이 큰 코인 자동 제외
- [x] #153 LLM 프롬프트 데이터 품질 개선 — 토큰 절약 + 피드백 루프 강화
- [ ] #154 뉴스 impact_score/scope 추가 — LLM 시장 판단 정밀도 향상
- [ ] #356 vwap_orb_breakout이 roi_table에 잘려 +0.8%에서 청산되는 버그 — 추세 진행 중 강제 매도 차단

### Phase 3: 인프라 + 운영 안정화 (예정)

- [x] #100 스케줄러 — 일일 헬스체크 (매매 정합성 + 뉴스 수집 + 미체결 정리)
- [x] #101 스케줄러 — 주간 리포트 (전략 성과 + DB 최적화 + 파라미터 추이)
- [x] #102 스케줄러 — 월간 감사 (거래 감사 + DB 백업 + 비용 정산)
- [x] #103 daily_report 개선 — 멀티코인 대응 + 포지션 가치 반영
- [ ] #80 워치독 서비스
- [x] #99 전략 객체 상태 공유 문제 (병렬화 대비)
- [ ] #98 JWT_SECRET 프로덕션 환경 강제 검증
- [ ] #20 Docker 컨테이너화
- [ ] #21 SQLite → PostgreSQL 마이그레이션

### Phase 4: 고도화 (예정)

- [ ] #19 백테스트 엔진
- [ ] #22 Airflow DAG
- [ ] #23 클라우드 배포
- [ ] #78 데이터 분석 파이프라인
- [ ] #79 자체 LLM 파인튜닝

### Phase 5: 벤치마킹 고도화 (예정)

[Freqtrade](https://github.com/freqtrade/freqtrade) · [Hummingbot](https://github.com/hummingbot/hummingbot) 참고

- [ ] #84 단계별 동적 손절 (Freqtrade custom_stoploss)
- [ ] #85 DCA 안전 주문 (Freqtrade adjust_trade_position)
- [ ] #86 Optuna 하이퍼파라미터 최적화 (Freqtrade Hyperopt)

### Phase 6: 멀티 마켓 확장 — 한국주식 + 미국주식 (진행 중)

코인 단일 → 코인 + 한국주식 + 미국주식 3개 시장 봇 확장. KIS Developers OpenAPI 기반.

- [ ] #243 [Epic] 멀티 마켓 봇 확장 — 한국주식 + 미국주식 추가
- [ ] #244 Exchange 추상화 레이어 리팩토링 (Upbit 어댑터 분리)
- [ ] #245 DB 스키마 마이그레이션 — `market` 컬럼 추가
- [ ] #246 한국주식 봇 — KIS OpenAPI 어댑터 + 듀얼모멘텀 전략
- [ ] #247 미국주식 봇 — KIS 해외주식 어댑터 + Clenow 추세추종
- [x] #279 KIS 봇 보수적 매매 전략 (RSI/MA 4중 매수 + 트레일링/추세 익절)
- [x] #281 KIS 봇 실 API 잔고로 매수 결정 (가정된 시드/환율 고정값 제거)

---

## 라이선스

MIT License
