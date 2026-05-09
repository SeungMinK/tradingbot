# CryptoBot — Claude 작업 가이드

## 프로젝트 개요

업비트 API 기반 코인 자동매매 봇. 변동성 돌파 전략 + Claude Haiku 시장분석.
상세 설계는 `docs/work/docs-work-README.md` 참조.

## 🎯 활성 전략 추적 — 작업 시작 시 필독

**`docs/active-strategy.md`** 가 *현재 어떤 매매 알고리즘이 돌고 있는지 / 왜 / 백테스트 결과* 의 단일 진입점.

### 작업 시작 시 (특히 매매 로직 / 전략 / 파라미터 관련)
- `docs/active-strategy.md` 먼저 읽고 현재 활성 전략 + 최근 변경 이력 + 후보 옵션 파악
- 현재 활성 옵션이 무엇인지(예: Option 1 = ORB 22:00 / 진입 5h / EOD 11:00)를 *코드 수정 전에* 확인

### 전략 / 파라미터 / 활성 옵션 변경 시 (필수)
변경 PR에 다음을 *반드시 같이 포함*:
1. `docs/active-strategy.md` 갱신
   - 현재 활성 전략 섹션 (핵심 파라미터, 백테스트 결과)
   - 변경 이력 표에 새 row 추가 (날짜 / PR# / 변경 / 사유)
   - 후보 옵션 표 갱신 (백테스트 결과 변경 시)
2. 백테스트 스크립트 결과 캡처 (또는 스크립트 파일 자체) — 다음 평가 시 재실행 가능하도록

이 문서가 갱신 안 된 채 운영 변경하면 다음 세션에서 *어떤 알고리즘이 돌고 있는지* 추적 불가.

## 기술 스택

- Python 3.11+
- SQLite (Phase 1) → PostgreSQL (Phase 3)
- pyupbit, pandas, numpy
- APScheduler, Slack Webhook, Streamlit

## 프로젝트 구조

```
src/cryptobot/
├── bot/            # 트레이딩 코어
├── data/           # DB, 수집, 기록
├── llm/            # Claude 연동 (Phase 2)
├── notifier/       # Slack 알림
├── dashboard/      # Streamlit (Phase 2)
├── backtest/       # 백테스트 (Phase 2)
└── scripts/        # 유틸리티
tests/              # 테스트 (src 구조 미러링)
```

## 코드 컨벤션

### 스타일

- 포매터: ruff format (line-length=120)
- 린터: ruff (rules: E, F, I)
- 따옴표: 큰따옴표 `"` 사용
- 들여쓰기: 스페이스 4칸
- 네이밍: snake_case (함수/변수), PascalCase (클래스), UPPER_SNAKE_CASE (상수)
- import 순서: 표준 라이브러리 → 서드파티 → 프로젝트 내부 (ruff I 룰이 자동 정렬)

### 타입 힌트

- 모든 함수의 파라미터와 리턴 타입에 타입 힌트 작성
- `Optional[X]` 대신 `X | None` 사용 (Python 3.10+ 스타일)

```python
def calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    ...
```

### 독스트링

- 모듈, 클래스, public 함수에 docstring 작성
- Google 스타일 docstring 사용

```python
def place_order(coin: str, side: str, amount: float) -> dict:
    """업비트에 주문을 실행한다.

    Args:
        coin: 종목 코드 (예: "KRW-BTC")
        side: 매수/매도 ("buy" / "sell")
        amount: 주문 수량

    Returns:
        업비트 API 응답 dict

    Raises:
        InsufficientBalanceError: 잔고 부족 시
    """
```

### 에러 처리

- API 호출(업비트, Claude, Slack)은 반드시 try/except로 감싸기
- 자체 예외 클래스는 `src/cryptobot/exceptions.py`에 정의
- 봇이 죽지 않도록 최상위 루프에서 catch-all 처리, 에러 시 Slack 알림

### 로깅

- `print()` 금지. 반드시 `logging` 모듈 사용
- 로그 레벨: DEBUG(지표 계산), INFO(매매 체결), WARNING(재시도), ERROR(API 실패)

```python
import logging
logger = logging.getLogger(__name__)
```

### 설정 관리

- 시크릿(API Key 등): `.env` 파일 → 절대 커밋 금지
- 매매 파라미터: DB `strategy_params` 테이블에서 로딩
- 앱 설정(로그 레벨, 스케줄 주기 등): `config.py`

### 테스트

- 테스트 프레임워크: pytest
- 테스트 파일: `tests/` 디렉토리에 `test_*.py`
- API 호출이 필요한 테스트는 mock 사용 (실제 API 호출 금지)
- 매매 전략 로직은 반드시 단위 테스트 작성
- **전략 전환 테스트 필수**: 전략 추가/수정/LLM 프롬프트 변경 시 `pytest tests/test_llm_strategy_switch.py -v` 실행 필수.
  이 테스트는 DB에서 전략을 동적으로 가져오므로 새 전략 추가 시 자동으로 커버됨.

### DB

- ORM 사용하지 않음. 직접 SQL 작성 (sqlite3 모듈)
- 테이블 스키마: `docs/work/docs-work-README.md`에 정의된 스키마를 그대로 사용
- 마이그레이션: `scripts/` 디렉토리에 SQL 스크립트로 관리

### Git 규칙

- 브랜치: `{이슈번호}/{타입}/{설명}` (예: `29/feature/fastapi-setup`)
- PR은 관련 이슈에 `Related: #번호`로 연결
- 상세 컨벤션: `CONVENTIONS.md` 참조

#### 커밋 메시지 접두사

| 접두사 | 용도 | 이슈 필요 | 예시 |
|--------|------|-----------|------|
| `hotfix:` | **프로덕션 긴급 수정** — 봇 크래시, 매매 중단, 데이터 유실 등 즉시 배포 필요 | O (main 직접 커밋, 이슈는 사전 또는 사후) | `hotfix: DataFrame bool 변환 에러 (#106)` |
| `fix:` | 일반 버그 수정 — 기능 오작동, UI 깨짐 등 | O | `fix: Pagination prop 이름 불일치 (#96)` |
| `feat:` | 새 기능 추가 | O | `feat: 대시보드에 최근 뉴스 섹션 추가 (#XX)` |
| `improve:` | 기존 기능 개선 — UX 개선, 표시 정보 추가/변경 등 | O | `improve: AI 시장 분석 제목에 시간 표시 (#XX)` |
| `refactor:` | 동작 변경 없는 코드 구조 개선 | O | `refactor: main.py 모듈 분리 (#90)` |
| `perf:` | 성능 최적화 | O | `perf: OHLCV 캐싱으로 API 호출 감소 (#XX)` |
| `test:` | 테스트 추가/수정 | △ | `test: 통합 테스트 15건 추가` |
| `docs:` | 문서, README, 주석 | X | `docs: README 로드맵 업데이트` |
| `chore:` | 설정, 빌드, 의존성 등 | X | `chore: .gitignore 업데이트` |

**판단 기준:**
- `hotfix:` vs `fix:` — 봇이 돌고 있는데 지금 당장 안 고치면 돈 잃거나 매매 못 하면 `hotfix:`
- `feat:` vs `improve:` — 완전히 새로운 기능이면 `feat:`, 기존 기능의 UX/정보 개선이면 `improve:`
- **모든 코드 변경은 이슈 필수** — 블로그 히스토리 추적용. hotfix는 긴급 시 커밋 먼저 → 사후 이슈 생성 허용.
- 이슈 없이 커밋 가능: `docs:`, `chore:` 만

## README.md 로드맵 관리 규칙

- **이슈 추가 시**: `README.md`의 Development Roadmap에 해당 Phase에 `- [ ] #번호 제목` 추가. 작업 순서와 Phase가 적절한지 판단해서 넣을 것.
- **이슈 Close 시**: 해당 항목을 `- [x]`로 변경.
- **Phase 완료 시**: 해당 Phase의 모든 이슈가 Close되고 신규 이슈가 없으면, Phase 제목에 `(완료)` 추가. (예: `### Phase 1: MVP — 자동매매 기본 동작 (완료)`)
- 미진행/진행중 상태는 별도 표기하지 않음. 체크박스로 완료 여부만 관리.

## 티스토리 자동 포스팅 (tistory-autoposter 연동)

이 프로젝트는 [tistory-autoposter](https://github.com/SeungMinK/tistory-autoposter)와 연동되어 있다.
이슈가 close되면 자동으로 AI가 블로그 글 작성 여부를 판단하고, 적합한 이슈를 묶어 개발일지를 발행한다.

### 동작 흐름

1. 이슈 close → GitHub Actions가 autoposter에 dispatch
2. AI Judge가 이슈를 판단 → `blog-적합` / `blog-부적합` 라벨 자동 부착
3. `blog-적합` 이슈가 2개 이상 쌓이면 → 묶어서 하나의 개발일지 작성
4. 글 간격은 1~3일 (하루 최대 1개, 3일을 넘기지 않음)
5. 이미 당일 발행된 글이 있으면 다음 날로 예약 발행
6. 발행 완료된 이슈에는 `blog-완료` 라벨 부착 + 코멘트

### 설정 파일

- `.autoposter.yml`: 프로젝트별 설정 (프리필터 규칙, AI 프롬프트, 발행 옵션 등)
- `.github/workflows/tistory-dispatch.yml`: dispatch 워크플로우

### 이슈 라벨

| 라벨 | 의미 |
|---|---|
| `blog-적합` | AI가 블로그 글로 작성할 가치 있다고 판단 |
| `blog-부적합` | AI가 블로그 글로 부적합하다고 판단 |
| `blog-완료` | 블로그 글에 포함되어 발행 완료 |

### 주의사항

- `blog-적합` 라벨이 달린 이슈를 수동으로 삭제하면 해당 이슈는 배치에서 빠진다
- 설정 변경은 `.autoposter.yml` 수정으로 가능 (커밋 필요)
- Secrets 필요: `AUTOPOSTER_PAT` (repo 스코프 PAT)

## 작업 시 주의사항

- 업비트 API Key는 **출금 권한 없이** 발급
- 실매매 코드 수정 시 반드시 백테스트 또는 테스트 먼저 실행
- 금액/수량 계산에서 부동소수점 주의 (Decimal 사용 검토)
- 모든 시간은 KST(Asia/Seoul) 기준으로 처리
