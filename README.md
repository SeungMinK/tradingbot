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
